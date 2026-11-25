"""Background writers keep expensive results through short database contention."""
import logging
import unittest
from unittest.mock import patch

from psycopg.errors import LockNotAvailable
from sqlalchemy.exc import OperationalError

from hiring_scraper.app import database


def lock_timeout():
    return OperationalError('UPDATE jobs', {}, LockNotAvailable('canceling statement due to lock timeout'))


class RetryTransientTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(database.time, 'sleep')
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_lock_timeout_is_retried_until_the_write_succeeds(self):
        calls = []

        def persist():
            calls.append(1)
            if len(calls) < 3:
                raise lock_timeout()
            return 'saved'

        with self.assertLogs('hiring_scraper.app.database', logging.WARNING):
            self.assertEqual(database.retry_transient(persist, action='saving a feed'), 'saved')
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_code_errors_are_not_retried(self):
        calls = []

        def persist():
            calls.append(1)
            raise KeyError('title')

        with self.assertRaises(KeyError):
            database.retry_transient(persist, action='saving a feed')
        self.assertEqual(len(calls), 1)

    def test_persistent_contention_is_raised_after_the_last_attempt(self):
        with self.assertRaises(OperationalError):
            database.retry_transient(lambda: (_ for _ in ()).throw(lock_timeout()),
                                     action='saving a feed', attempts=2)
        self.assertEqual(self.sleep.call_count, 1)

    def test_extra_retryable_exceptions(self):
        class Changed(RuntimeError):
            pass

        calls = []

        def persist():
            calls.append(1)
            if len(calls) == 1:
                raise Changed()
            return len(calls)

        self.assertEqual(database.retry_transient(persist, action='x', retry_on=(Changed,)), 2)


class WorkerFailureLogTests(unittest.TestCase):
    def test_contention_logs_one_line_without_traceback(self):
        logger = logging.getLogger('radius.test.contention')
        with self.assertLogs(logger, logging.WARNING) as logs:
            database.log_worker_failure(logger, 'Cycle failed', lock_timeout())
        self.assertEqual(logs.records[0].levelno, logging.WARNING)
        self.assertIsNone(logs.records[0].exc_info)
        self.assertIn('lock timeout', logs.output[0])

    def test_unexpected_errors_keep_their_traceback(self):
        logger = logging.getLogger('radius.test.bug')
        with self.assertLogs(logger, logging.ERROR) as logs:
            database.log_worker_failure(logger, 'Cycle failed', ValueError('bad data'))
        self.assertIsNotNone(logs.records[0].exc_info)


if __name__ == '__main__':
    unittest.main()
