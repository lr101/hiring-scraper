"""Background local-model pipeline: analysis, CV units and pair evidence reach Journey."""
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import job_analysis_worker as worker
from hiring_scraper.app import job_search, models, source_changes
from hiring_scraper.app.match_compute import compute_match
from hiring_scraper.app.match_documents import load_match_documents
from tests import pg

PREFERENCES = {
    'desired_roles': ['Backend Developer'],
    'skills': ['Python', 'PostgreSQL'],
    'summary': 'Backend developer building Python services on PostgreSQL.',
    'search_area': {'latitude': 49.0069, 'longitude': 8.4037, 'radius_km': 25, 'city': 'Karlsruhe',
                    'country_code': 'DE'},
    'matching_defaults': {'min_match_score': 0, 'include_unknown': True},
}
LLM_OUTPUT = ('OCC backend software development\nIND software\nDUTIES\nD 1 | Build Python services.\n'
              'REQUIREMENTS\nR 2 must experience | Experience with PostgreSQL.\nEND')


def fake_embed(texts, kind):
    return [[1.0, float(len(text) % 7), float('python' in text.casefold())] + [0.0] * 381 for text in texts]


def fake_rerank(query, documents):
    return [0.999 if 'python' in doc.casefold() or 'postgres' in doc.casefold() or 'developer' in doc.casefold()
            else 0.01 for doc in documents]


class JobAnalysisWorkerTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self)
        self.factory = sessionmaker(self.engine, autoflush=False, expire_on_commit=False)
        with self.factory.begin() as session:
            profile = models.UserProfile(name='Developer', preferences=PREFERENCES)
            feed = models.JobFeed(company=models.Company(source='test', source_id='c', name='Employer'),
                                  provider='rss', status='parsed', feed_url='https://example.test/jobs')
            near = models.Job(feed=feed, external_id='near', title='Backend Developer',
                              url='https://example.test/near',
                              description='Build Python services.\nExperience with PostgreSQL.')
            near.locations.append(models.JobLocation(label='Karlsruhe', latitude=49.01, longitude=8.40,
                                                     country_code='DE'))
            far = models.Job(feed=feed, external_id='far', title='Backend Developer',
                             url='https://example.test/far',
                             description='Build Python services.\nExperience with PostgreSQL.')
            far.locations.append(models.JobLocation(label='Hamburg', latitude=53.55, longitude=9.99,
                                                    country_code='DE'))
            session.add_all([profile, feed, near, far])
            session.flush()
            # The far job is newer, but the in-area job must be analysed first.
            self.profile_id, self.near_id, self.far_id = profile.id, near.id, far.id
            source_changes.jobs_changed(session, [near.id, far.id])
        self.patches = [patch.object(worker, 'SessionLocal', self.factory),
                        patch.object(job_search, 'SessionLocal', self.factory)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def match(self, job_id):
        with self.factory() as session:
            job = session.get(models.Job, job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            return compute_match(job, profile, load_match_documents(session, [job], [profile]))

    def test_in_area_job_is_analysed_first_and_becomes_visible_after_evidence(self):
        from hiring_scraper.app import api
        self.assertTrue(self.match(self.near_id)['pending'])
        with self.factory() as session:
            page = api.list_applications(profile_ids=str(self.profile_id), statuses='all', session=session)
            self.assertEqual(page['analysis_backlog'], 2)
        self.assertEqual(worker.analyse_next_job(analyse=lambda lines: LLM_OUTPUT), self.near_id)
        with self.factory() as session:
            row = session.get(models.JobAnalysis, self.near_id)
            self.assertEqual(row.status, 'ok')
            self.assertEqual([d['text'] for d in row.payload['duties']], ['Build Python services.'])
            self.assertIn(self.near_id, session.scalars(select(models.MatchDirtyJob.job_id)).all())
        # Analysis alone is not enough: the pair evidence is still missing.
        self.assertTrue(self.match(self.near_id)['pending'])
        self.assertTrue(worker.refresh_profile_units_once(embed_fn=fake_embed))
        self.assertEqual(worker.refresh_evidence_once(embed_fn=fake_embed, rerank_fn=fake_rerank), 1)
        result = self.match(self.near_id)
        self.assertFalse(result['pending'])
        self.assertGreaterEqual(result['score'], 65)
        # Journey picks the job up from the rematch queue.
        self.assertTrue(job_search.refresh_dirty_job_matches_once())
        with self.factory() as session:
            states = session.scalars(select(models.JobApplication.job_id)).all()
            self.assertEqual(states, [self.near_id])

    def test_failed_analysis_backs_off_and_edit_during_inference_is_discarded(self):
        def broken(lines):
            raise ValueError('malformed answer')
        with self.assertLogs(worker.LOG, level='WARNING'):
            self.assertEqual(worker.analyse_next_job(analyse=broken), self.near_id)
        with self.factory() as session:
            row = session.get(models.JobAnalysis, self.near_id)
            self.assertEqual(row.status, 'failed')
            self.assertIsNotNone(row.retry_at)
        # The failed job waits for its retry time; the next job is analysed meanwhile.
        def edit_during_inference(lines):
            with self.factory.begin() as session:
                session.get(models.Job, self.far_id).description = 'Completely different text.'
                source_changes.jobs_changed(session, [self.far_id])
            return LLM_OUTPUT
        self.assertEqual(worker.analyse_next_job(analyse=edit_during_inference), self.far_id)
        with self.factory() as session:
            self.assertIsNone(session.get(models.JobAnalysis, self.far_id))

    def test_unavailable_models_raise_os_error_for_backoff(self):
        def offline(lines):
            raise OSError('connection refused')
        with self.assertRaises(OSError):
            worker.analyse_next_job(analyse=offline)
        with self.factory() as session:
            self.assertIsNone(session.get(models.JobAnalysis, self.near_id))

    def test_job_edit_keeps_old_analysis_in_use_until_replaced(self):
        worker.analyse_next_job(analyse=lambda lines: LLM_OUTPUT)
        worker.refresh_profile_units_once(embed_fn=fake_embed)
        worker.refresh_evidence_once(embed_fn=fake_embed, rerank_fn=fake_rerank)
        with self.factory.begin() as session:
            session.get(models.Job, self.near_id).description = 'Build Python services.\nKubernetes required.'
            source_changes.jobs_changed(session, [self.near_id])
        with self.factory() as session:
            self.assertEqual(session.get(models.JobAnalysis, self.near_id).status, 'stale')
        self.assertFalse(self.match(self.near_id)['pending'])
        # Both jobs need work; the in-area stale job comes first.
        self.assertEqual(worker.analyse_next_job(analyse=lambda lines: LLM_OUTPUT.replace(
            'Experience with PostgreSQL.', 'Kubernetes required.')), self.near_id)
        with self.factory() as session:
            self.assertEqual(session.get(models.JobAnalysis, self.near_id).status, 'ok')


if __name__ == '__main__':
    unittest.main()
