import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hiring_scraper.app import application_generation_jobs as jobs
from tests import pg


class AutomaticPreparationTests(unittest.TestCase):
    def test_automatic_completed_result_survives_retention_period(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobs.start(root, 1, 2, lambda progress: {'package': {'cv': 'prepared'}}, automatic=True)
            for _ in range(100):
                result = jobs.status(root, 1, 2)
                if result['state'] == 'completed':
                    break
                time.sleep(.01)
            self.assertEqual(result['state'], 'completed')
            with patch.object(jobs.time, 'time', return_value=time.time() + jobs.RETENTION_SECONDS + 1):
                self.assertEqual(jobs.status(root, 1, 2)['result']['package']['cv'], 'prepared')

    def test_language_uses_posting_and_available_cv(self):
        from hiring_scraper.app.auto_preparation import document_language
        self.assertEqual(document_language('Wir suchen Sie und Ihre Kenntnisse für unsere Aufgaben', {'de': {}, 'en': {}}), 'de')
        self.assertEqual(document_language('We are looking for you and your experience', {'de': {}, 'en': {}}), 'en')
        self.assertEqual(document_language('We are looking for you', {'de': {}}), 'de')

class SchedulerTests(unittest.TestCase):
    def test_selects_highest_ranked_new_and_preserves_existing_draft(self):
        from sqlalchemy.orm import sessionmaker
        from hiring_scraper.app import auto_preparation as auto, models
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            engine = pg.engine(self)
            models.Base.metadata.create_all(engine)
            factory = sessionmaker(engine)
            with factory() as session:
                profile = models.UserProfile(name='Test', preferences={})
                company = models.Company(source='test', source_id='company', name='Example')
                feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.test')
                session.add_all([profile, feed]); session.flush()
                session.add(models.ProfileCV(profile_id=profile.id, filename='cv.tex', content_type='text/plain', size_bytes=1, data=b'x', extracted_text='CV'))
                for index in range(26):
                    job = models.Job(feed=feed, external_id=str(index), title='Engineer', description='We are looking for you', url=f'https://example.test/{index}')
                    session.add(job); session.flush()
                    session.add(models.JobApplication(profile_id=profile.id, job_id=job.id, status='new'))
                    session.add(models.ProfileJobMatch(profile_id=profile.id, job_id=job.id, match_score=index, match_tier='good', match_uncertain=False))
                session.commit()
                profile_id = profile.id
                highest = job.id
            jobs.save_draft(root, profile_id, highest, {'cv': 'edited'}, None)
            statuses = {}
            queue = auto.application_preparation_queue
            with patch.object(auto, 'SessionLocal', factory), patch.object(auto.codex_runtime, '_root', return_value=root), \
                 patch.object(auto.codex_runtime, 'status', return_value={'enabled': True}), \
                 patch.object(auto.codex_runtime, 'subscription_usage', return_value={'allowed': True, 'used_percent': 75}), \
                 patch.object(auto.codex_runtime, 'selected_model', return_value=None), \
                 patch.object(auto, 'profile_match_index_stale', return_value=False), \
                 patch.object(auto.documents, 'pipeline_version', return_value='v1'), \
                 patch.object(auto.generation, '_snapshot', return_value=({}, 'revision')), \
                 patch.object(queue, 'has_active', return_value=False), \
                 patch.object(queue, 'status', side_effect=lambda _session, _profile, job_id: statuses.get(job_id)), \
                 patch.object(queue, 'enqueue') as enqueue:
                # The best-ranked role already has a manual draft, so the next one is queued.
                self.assertTrue(auto.prepare_once())
                self.assertEqual(enqueue.call_args.args[1], highest - 1)
                self.assertTrue(enqueue.call_args.kwargs['automatic'])
                # A completed automatic preparation with stale inputs is refreshed.
                statuses[highest - 1] = {'state': 'completed', 'automatic': True, 'revision': 'old',
                                         'updated_at': time.time()}
                enqueue.reset_mock()
                self.assertTrue(auto.prepare_once())
                self.assertEqual(enqueue.call_args.args[1], highest - 1)
                # Once every top role has a draft there is nothing to prepare.
                for job_id in range(highest - 24, highest):
                    jobs.save_draft(root, profile_id, job_id, {'cv': 'edited'}, None)
                enqueue.reset_mock()
                self.assertFalse(auto.prepare_once())
                enqueue.assert_not_called()
            engine.dispose()
