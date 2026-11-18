"""Exercise the durable preparation lifecycle without calling the model."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import application_preparation_queue as queue
from hiring_scraper.app.models import ApplicationPreparationTask, Base, utcnow


class ApplicationPreparationQueueTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine, tables=[ApplicationPreparationTask.__table__])
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        factory = patch.object(queue, 'SessionLocal', self.sessions)
        factory.start()
        self.addCleanup(factory.stop)

    def test_expired_worker_is_fenced_and_replacement_resumes_checkpoints(self):
        record = queue.enqueue(1, 2, {'job_id': 2}, 'rev')
        self.assertEqual(queue.enqueue(1, 2, {'job_id': 2}, 'rev')['id'], record['id'])
        key, token, _ = queue._claim()
        expired = queue.Progress(key, token, {})
        expired.checkpoint('draft', {'title': 'Saved work'})
        self.assertIsNone(queue._claim())
        with self.sessions.begin() as session:
            session.get(ApplicationPreparationTask, key).lease_until = utcnow() - timedelta(seconds=1)

        def run(profile_id, request, revision, progress):
            self.assertEqual((profile_id, request, revision), (1, {'job_id': 2}, 'rev'))
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
        record = queue.enqueue(1, 2, {'job_id': 2}, 'rev')
        with self.sessions() as session:
            cancelled = queue.cancel(session, 1, 2, record['id'])
        self.assertEqual(cancelled['state'], 'cancelled')
        self.assertIsNone(queue._claim())
