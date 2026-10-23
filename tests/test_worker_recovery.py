"""Database contention must back off a worker cycle without killing the service."""
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from hiring_scraper.app import discovery_worker, profile_match_worker, worker
from hiring_scraper.app.job_search import ProfileIndexChanged


class LockTimeout(Exception):
    sqlstate = '55P03'


class WorkerRecoveryTests(unittest.TestCase):
    def test_profile_match_loop_retries_conflicts_without_exiting(self):
        with patch.object(profile_match_worker, 'refresh_profile_match_indexes_once',
                          side_effect=[ProfileIndexChanged('inventory changed'), KeyboardInterrupt]), \
                patch.object(profile_match_worker.time, 'sleep'), \
                self.assertLogs(profile_match_worker.LOG, level='WARNING'):
            with self.assertRaises(KeyboardInterrupt):
                profile_match_worker.main()

    def test_feed_loop_survives_a_database_lock_timeout(self):
        failure = OperationalError('claim feed', {}, LockTimeout('lock timeout'))
        with patch.object(worker, 'ensure_default_job_feeds', return_value=0), \
                patch.object(worker, 'scan_once', side_effect=[failure, KeyboardInterrupt]), \
                patch.object(worker.time, 'sleep'), self.assertLogs(worker.LOG, level='WARNING'):
            with self.assertRaises(KeyboardInterrupt):
                worker.main()

    def test_discovery_loop_survives_a_database_lock_timeout(self):
        failure = OperationalError('claim company', {}, LockTimeout('lock timeout'))
        with patch('sys.argv', ['discovery-worker']), \
                patch.object(discovery_worker, 'process_batch', side_effect=[failure, KeyboardInterrupt]), \
                patch.object(discovery_worker.time, 'sleep'), self.assertLogs(discovery_worker.LOG, level='WARNING'):
            with self.assertRaises(KeyboardInterrupt):
                discovery_worker.main()

    def test_batch_processes_claims_already_owned_when_a_later_claim_times_out(self):
        failure = OperationalError('claim company', {}, LockTimeout('lock timeout'))
        processed = []
        claim = (1, 2, None, None)
        with patch.object(discovery_worker, '_prepare_cycle', return_value=None), \
                patch.object(discovery_worker, '_claim_campaign_company', side_effect=[claim, failure]), \
                patch.object(discovery_worker, '_claim_due_homepage_resolution', return_value=None), \
                patch.object(discovery_worker, '_process_claimed_company', side_effect=lambda item: processed.append(item)), \
                self.assertLogs(discovery_worker.LOG, level='WARNING'):
            self.assertTrue(discovery_worker.process_batch(location_jobs_only=True, max_workers=2))
        self.assertEqual(processed, [claim])
