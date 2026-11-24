"""Exercise the durable preparation lifecycle without calling the model."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import application_preparation_queue as queue
from hiring_scraper.app.models import ApplicationPreparationTask, Company, Job, JobFeed, UserProfile, utcnow
from tests import pg


class ApplicationPreparationQueueTests(unittest.TestCase):
    def setUp(self):
        engine = pg.engine(self)
        self.addCleanup(engine.dispose)
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        # Queue rows reference a real profile (1) and job (2).
        with self.sessions.begin() as session:
            session.add(UserProfile(id=1, name='Queue'))
            feed = JobFeed(company=Company(source='test', source_id='queue', name='Employer'),
                           provider='rss', feed_url='https://example.test/jobs.xml', status='parsed')
            session.add(feed)
            session.flush()
            session.add_all([Job(id=job_id, feed_id=feed.id, external_id=str(job_id), title='Role',
                                 url=f'https://example.test/{job_id}') for job_id in (2, 3, 4)])
        factory = patch.object(queue, 'SessionLocal', self.sessions)
        factory.start()
        self.addCleanup(factory.stop)

    def test_expired_worker_is_fenced_and_replacement_resumes_checkpoints(self):
        record = queue.enqueue_task(1, 2, 'prepare', {'job_id': 2})
        self.assertEqual(queue.enqueue_task(1, 2, 'prepare', {'job_id': 2})['id'], record['id'])
        key, token, _ = queue._claim()
        expired = queue.Progress(key, token, {})
        expired.checkpoint('draft', {'title': 'Saved work'})
        self.assertIsNone(queue._claim())
        with self.sessions.begin() as session:
            session.get(ApplicationPreparationTask, key).lease_until = utcnow() - timedelta(seconds=1)

        def run(profile_id, request, progress):
            self.assertEqual((profile_id, request), (1, {'job_id': 2}))
            self.assertEqual(progress.load('draft'), {'title': 'Saved work'})
            self.assertTrue(expired.cancelled())
            self.assertFalse(expired._update(finish=True, state='failed'))
            return {'package': 'replacement result'}

        self.assertTrue(queue.process_once(run))
        with self.sessions() as session:
            final = queue.status(session, *key)
        self.assertEqual(final['state'], 'completed')
        self.assertEqual(final['result'], {'package': 'replacement result'})

    def test_unclaimed_cancellation_prevents_execution(self):
        record = queue.enqueue_task(1, 2, 'prepare', {'job_id': 2})
        with self.sessions() as session:
            cancelled = queue.cancel(session, 1, 2, record['id'])
        self.assertEqual(cancelled['state'], 'cancelled')
        self.assertIsNone(queue._claim())
