"""Profile/job vector refresh finds missing vectors without scanning the inventory."""
import unittest
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import profile_semantics
from hiring_scraper.app.models import Base, Company, Job, JobFeed, MatchSemanticDocument
from hiring_scraper.semantic_matching import VERSION
from tests import pg


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class VectorSweepTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.clock = Clock()
        for item in (patch.object(profile_semantics, 'JOB_SWEEP_WINDOW', 10),
                     patch.object(profile_semantics, 'time', self.clock)):
            item.start()
            self.addCleanup(item.stop)
        with self.factory.begin() as session:
            feed = JobFeed(company=Company(source='t', source_id='c', name='C', career_status='checked'),
                           provider='rss', feed_url='https://example.test/feed', status='parsed')
            session.add(feed)
            session.flush()
            self.feed_id = feed.id
        self.add_jobs(1, 50, vectors=True)

    def add_jobs(self, first, last, *, vectors):
        with self.factory.begin() as session:
            for job_id in range(first, last + 1):
                session.add(Job(id=job_id, feed_id=self.feed_id, external_id=str(job_id), title='Engineer',
                                url=f'https://example.test/{job_id}', is_active=True))
                if vectors:
                    session.add(MatchSemanticDocument(job_id=job_id, source_hash='h', version=VERSION,
                                                      sections=[], vectors=b''))

    def missing(self, limit=4):
        with self.factory() as session:
            return [job.id for job in profile_semantics._jobs_missing_vectors(session, limit)]

    def test_new_jobs_are_found_immediately_newest_first(self):
        self.assertEqual(self.missing(), [])
        self.add_jobs(51, 53, vectors=False)
        self.assertEqual(self.missing(), [53, 52, 51])

    def test_an_old_job_without_vectors_is_found_by_the_sweep(self):
        with self.factory.begin() as session:
            session.query(MatchSemanticDocument).filter(MatchSemanticDocument.job_id == 7).delete()
        found = []
        for _ in range(10):
            found += self.missing()
            self.clock.now += profile_semantics.JOB_SWEEP_IDLE_SECONDS
        self.assertIn(7, found)

    def test_clean_inventory_pauses_between_windows(self):
        from sqlalchemy import event

        window_scans = []

        def record(_conn, _cursor, statement, *_args):
            if 'jobs.id >' in statement:
                window_scans.append(statement)

        self.assertEqual(self.missing(), [])  # high-water check and first sweep window
        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            self.assertEqual(self.missing(), [])
            self.assertEqual(window_scans, [], 'A clean inventory must not rescan before the pause')
            self.clock.now += profile_semantics.JOB_SWEEP_IDLE_SECONDS
            self.assertEqual(self.missing(), [])
            self.assertEqual(len(window_scans), 1, 'One bounded window per pause')
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)

    def test_edited_jobs_in_the_rematch_queue_are_found_immediately(self):
        from hiring_scraper.app.models import MatchDirtyJob

        self.assertEqual(self.missing(), [])
        with self.factory.begin() as session:
            session.query(MatchSemanticDocument).filter(MatchSemanticDocument.job_id == 3).delete()
            session.merge(MatchDirtyJob(job_id=3, revision='r'))
        self.assertEqual(self.missing(), [3])

if __name__ == '__main__':
    unittest.main()
