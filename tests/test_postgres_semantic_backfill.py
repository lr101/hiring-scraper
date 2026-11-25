"""Semantic search backfill stays bounded on PostgreSQL; use a disposable TEST_POSTGRES_URL."""
import unittest
from unittest.mock import patch

from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import semantic_search_worker as worker
from hiring_scraper.app.models import (
    Company, Job, JobFeed, SemanticSearchDocument, SemanticSearchIndexState, SemanticSearchRefresh,
)
from hiring_scraper.semantic_matching import MODEL_KEY
from tests import pg


class FakeEmbedder:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError('model crashed')
        return [[0.01] * worker.DIMENSIONS for _ in texts]


class SemanticBackfillTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self, connect_args={'options': '-c statement_timeout=5000'})
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [
            patch.object(worker, 'SessionLocal', self.factory),
            patch.object(worker, 'BACKFILL_WINDOW', 100),
            patch.object(worker, 'INDEX_BATCH_SIZE', 8),
            patch.dict(worker._backfill_cursor, {'company': 0, 'job': 0}),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def seed(self, jobs, spacing=1):
        """Create rows as if they predate the semantic index."""
        with self.factory.begin() as session:
            company = Company(source='test', source_id='c', name='Employer', career_status='unknown')
            feed = JobFeed(company=company, provider='rss', feed_url='https://example.test/feed', status='ok')
            session.add(feed)
            session.flush()
            session.add_all(Job(id=(index + 1) * spacing, feed_id=feed.id, external_id=str(index),
                                title=f'Engineer {index}', url=f'https://example.test/{index}')
                            for index in range(jobs))
        with self.factory.begin() as session:
            # Database triggers queue new rows; these must look like pre-index inventory.
            session.execute(text('DELETE FROM semantic_search_refreshes'))

    def indexed(self, entity_type):
        with self.factory() as session:
            return set(session.scalars(select(SemanticSearchDocument.entity_id).where(
                SemanticSearchDocument.entity_type == entity_type,
                SemanticSearchDocument.model_revision == MODEL_KEY)))

    def complete(self, entity_type):
        with self.factory() as session:
            return bool(session.scalar(select(SemanticSearchIndexState.backfill_complete).where(
                SemanticSearchIndexState.entity_type == entity_type,
                SemanticSearchIndexState.model_revision == MODEL_KEY)))

    def drain(self, limit=200):
        for _ in range(limit):
            if not worker.process_index_batch():
                return
        self.fail('backfill did not finish')

    def test_backfill_indexes_sparse_ids_across_windows_then_stops_scanning(self):
        # Gaps wider than one window must not end the pass early.
        self.seed(30, spacing=370)
        with patch.object(worker, '_embedder', FakeEmbedder()):
            self.drain()
            with self.factory() as session:
                job_ids = set(session.scalars(select(Job.id)))
            self.assertEqual(self.indexed('job'), job_ids)
            self.assertEqual(len(self.indexed('company')), 1)
            self.assertTrue(self.complete('job'))
            self.assertTrue(self.complete('company'))

            statements = []
            listener = lambda *args: statements.append(args[2])
            event.listen(self.engine, 'before_cursor_execute', listener)
            try:
                self.assertFalse(worker.process_index_batch())
            finally:
                event.remove(self.engine, 'before_cursor_execute', listener)
        self.assertFalse([sql for sql in statements if 'FROM jobs' in sql],
                         'A completed backfill must not rescan the job inventory')

    def test_failed_embedding_does_not_skip_rows(self):
        self.seed(5)
        with patch.object(worker, '_embedder', FakeEmbedder(fail=True)):
            with self.assertRaises(RuntimeError):
                worker.process_index_batch()
        self.assertFalse(self.complete('job'))
        with patch.object(worker, '_embedder', FakeEmbedder()):
            self.drain()
        self.assertEqual(len(self.indexed('job')), 5)

    def test_queued_rows_are_left_to_the_refresh_queue(self):
        self.seed(3)
        with self.factory.begin() as session:
            first = session.scalar(select(Job.id).order_by(Job.id))
            session.add(SemanticSearchRefresh(entity_type='job', entity_id=first))
        with patch.object(worker, '_embedder', FakeEmbedder()):
            self.drain()
        self.assertEqual(len(self.indexed('job')), 3)
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(SemanticSearchRefresh.entity_id)))


if __name__ == '__main__':
    unittest.main()
