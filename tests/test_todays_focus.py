"""Today's focus offers three achievable next steps instead of the whole Journey."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.orm import Session

from hiring_scraper.app import api, focus, models, profiles
from tests import pg


class TodaysFocusTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self)
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.profile = profiles.create_profile(profiles.ProfileRequest(name='Developer'), self.session)
        company = models.Company(source='test', source_id='employer', name='Employer')
        feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.test/jobs',
                              status='parsed')
        self.jobs = [models.Job(feed=feed, external_id=str(index), title=f'Role {index}',
                                url=f'https://example.test/jobs/{index}', is_remote=True)
                     for index in range(6)]
        self.session.add(company)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def track(self, job, status='new', score=None):
        api.set_job_application(self.profile['id'], job.id, api.ApplicationRequest(status=status), self.session)
        if score is not None:
            self.session.merge(models.ProfileJobMatch(profile_id=self.profile['id'], job_id=job.id,
                                                      match_score=score, match_tier='possible',
                                                      match_uncertain=False))
            self.session.commit()

    def focus(self):
        return focus.get_todays_focus(self.profile['id'], self.session)

    def test_picks_three_actionable_roles_ready_then_interested_then_best_match(self):
        self.track(self.jobs[0], score=40)
        self.track(self.jobs[1], score=90)
        self.track(self.jobs[2], score=70)
        self.track(self.jobs[3], 'open', score=20)
        self.track(self.jobs[4], 'waiting_for_reply', score=99)  # already sent
        ready = self.jobs[0].id
        with patch.object(focus, '_documents_ready', side_effect=lambda _s, _p, job_id: job_id == ready):
            result = self.focus()
        self.assertEqual([item['job_id'] for item in result['items']],
                         [self.jobs[0].id, self.jobs[3].id, self.jobs[1].id])
        self.assertTrue(result['items'][0]['documents_ready'])
        self.assertIn('ready', result['items'][0]['reason'])
        self.assertIn('Interested', result['items'][1]['reason'])
        self.assertEqual(result['items'][2]['match_score'], 90)
        self.assertEqual(result['daily_target'], 3)

    def test_closed_and_expired_postings_are_skipped(self):
        self.track(self.jobs[0], score=80)
        self.track(self.jobs[1], score=60)
        job = self.session.get(models.Job, self.jobs[0].id)
        job.is_active = False
        self.session.commit()
        self.assertEqual([item['job_id'] for item in self.focus()['items']], [self.jobs[1].id])

    def test_sending_three_today_completes_the_day_once_per_vacancy(self):
        for job in self.jobs[:3]:
            self.track(job)
        self.assertEqual(self.focus()['sent_today'], 0)
        self.track(self.jobs[0], 'waiting_for_reply')
        self.track(self.jobs[1], 'automated_application')
        # Switching between sent stages must not count the same application twice.
        self.track(self.jobs[1], 'waiting_for_reply')
        result = self.focus()
        self.assertEqual(result['sent_today'], 2)
        self.assertFalse(result['completed'])
        self.track(self.jobs[2], 'waiting_for_reply')
        self.assertTrue(self.focus()['completed'])

    def test_applications_sent_on_earlier_days_do_not_count_today(self):
        self.track(self.jobs[0])
        self.track(self.jobs[0], 'waiting_for_reply')
        event = self.session.query(models.ApplicationMomentumEvent).one()
        event.occurred_at = event.occurred_at - timedelta(days=1)
        self.session.commit()
        self.assertEqual(self.focus()['sent_today'], 0)

    def test_both_current_structured_documents_count_as_ready(self):
        from pathlib import Path
        from hiring_scraper.app import application_documents_v2 as documents
        from hiring_scraper.documents import import_marked_tex
        from tests.test_application_documents_v2_unit import letter

        self.track(self.jobs[0], score=10)
        self.track(self.jobs[1], score=95)
        profile_id = self.profile['id']
        job_id = self.jobs[0].id
        documents.save_version(self.session, profile_id, job_id, 'letter', letter(), expected_version=0, origin='user')
        self.session.commit()
        self.assertEqual(self.focus()['items'][0]['job_id'], self.jobs[1].id)

        source = Path(__file__).with_name('fixtures') / 'documents' / 'flowing_en_cv.tex'
        cv = import_marked_tex(source.read_text()).model_dump()
        documents.save_version(self.session, profile_id, job_id, 'cv', cv, expected_version=0, origin='user')
        self.session.commit()
        self.assertEqual(self.focus()['items'][0]['job_id'], job_id)

        documents.mark_stale(self.session, profile_id, job_id)
        self.session.commit()
        self.assertEqual(self.focus()['items'][0]['job_id'], self.jobs[1].id)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(HTTPException):
            focus.get_todays_focus(9999, self.session)


if __name__ == '__main__':
    unittest.main()
