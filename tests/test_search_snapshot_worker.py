"""Dependency boundary for the standalone snapshot worker."""

import subprocess
import sys
import unittest


class SearchSnapshotWorkerTests(unittest.TestCase):
    def test_worker_import_does_not_load_http_api(self):
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; import hiring_scraper.app.search_snapshot_worker; "
                "assert 'hiring_scraper.app.api' not in sys.modules",
            ],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_queued_refresh_runs_during_full_backfill_batches(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        from hiring_scraper.app import search_snapshot_worker as worker, search_snapshots
        from hiring_scraper.app.models import Company, Job, JobFeed, JobSearchRefresh, JobSearchSnapshot
        from tests import pg

        engine = pg.engine(self)
        with Session(engine) as session:
            feed = JobFeed(
                company=Company(source='test', source_id='backlog', name='Employer'),
                provider='rss', feed_url='https://backlog.example/feed', status='parsed',
            )
            session.add_all([
                Job(feed=feed, external_id=str(i), title='Engineer', url=f'https://backlog.example/{i}')
                for i in range(5)
            ])
            session.commit()
            search_snapshots.enqueue_search_snapshot_refresh(session, {
                'latitude': None, 'longitude': None, 'radius_km': None, 'place': None,
                'country': 'DE', 'location_scope': 'area_remote', 'state': None,
                'employer_country': 'DE', 'company_id': None, 'query': None,
                'work_style': 'all', 'posting_status': 'open',
            })

        class StopWorker(Exception):
            pass

        # Run two full backfill batches, leaving one job in the rollout backlog.
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0.0, 1.0, StopWorker()]))
        with (
            pg.patched_session_local(engine),
            patch.object(worker, 'time', clock),
            patch.object(worker, 'BACKFILL_BATCH_SIZE', 2),
            patch.object(search_snapshots, '_SEARCH_SNAPSHOT_REFRESH_DELAY_SECONDS', 0),
            self.assertRaises(StopWorker),
        ):
            worker.main()

        with Session(engine) as session:
            self.assertGreater(session.scalar(select(func.count()).select_from(Job).where(
                Job.location_scope_version.is_(None)
            )), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(JobSearchRefresh)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(JobSearchSnapshot)), 1)
