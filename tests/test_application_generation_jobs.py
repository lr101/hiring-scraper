import tempfile
import os
import threading
import time
import unittest
from pathlib import Path
from hiring_scraper.app import application_generation_jobs as jobs


class GenerationJobTests(unittest.TestCase):
    def test_running_job_survives_reader_reload_and_duplicate_start(self):
        with tempfile.TemporaryDirectory() as folder:
            started, finish = threading.Event(), threading.Event()
            def run(progress):
                progress('reviewing')
                started.set()
                finish.wait(5)
                return {'package': {'cv': 'finished'}}
            first = jobs.start(Path(folder), 1, 2, run)
            self.assertTrue(started.wait(2))
            self.assertEqual(jobs.status(Path(folder), 1, 2)['stage'], 'reviewing')
            second = jobs.start(Path(folder), 1, 2, lambda _: self.fail('duplicate generation'))
            self.assertEqual(first['id'], second['id'])
            finish.set()
            for _ in range(100):
                state = jobs.status(Path(folder), 1, 2)
                if state['state'] == 'completed': break
                time.sleep(.01)
            self.assertEqual(state['result']['package']['cv'], 'finished')

    def test_failure_and_interrupted_job_have_recoverable_state(self):
        with tempfile.TemporaryDirectory() as folder:
            def fail(_): raise RuntimeError('private details')
            jobs.start(Path(folder), 1, 2, fail)
            for _ in range(100):
                state = jobs.status(Path(folder), 1, 2)
                if state['state'] == 'failed': break
                time.sleep(.01)
            self.assertEqual(state['state'], 'failed')
            self.assertNotIn('private details', str(state))
            jobs._write(Path(folder), 1, 2, {'id': 'old', 'state': 'running', 'updated_at': time.time()})
            self.assertEqual(jobs.status(Path(folder), 1, 2)['state'], 'failed')

    def test_large_unicode_result_and_expired_other_job_are_handled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobs._write(root, 3, 4, {'id': 'expired', 'state': 'completed', 'updated_at': time.time() - jobs.RETENTION_SECONDS - 1})
            os.utime(jobs._path(root, 3, 4), (time.time() - jobs.RETENTION_SECONDS - 1,) * 2)
            jobs.start(root, 1, 2, lambda _: {'package': {'cv': '德' * 190000}})
            for _ in range(100):
                state = jobs.status(root, 1, 2)
                if state and state['state'] == 'completed': break
                time.sleep(.01)
            self.assertEqual(len(state['result']['package']['cv']), 190000)
            self.assertFalse(jobs._path(root, 3, 4).exists())

    def test_queue_capacity_is_bounded(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as folder, patch.object(jobs, '_capacity', threading.BoundedSemaphore(1)):
            started, finish = threading.Event(), threading.Event()
            def run(_):
                started.set()
                finish.wait(5)
                return {}
            jobs.start(Path(folder), 1, 1, run)
            self.assertTrue(started.wait(2))
            with self.assertRaises(HTTPException) as error:
                jobs.start(Path(folder), 1, 2, run)
            self.assertEqual(error.exception.status_code, 429)
            finish.set()
            for _ in range(100):
                if jobs.status(Path(folder), 1, 1)['state'] == 'completed': break
                time.sleep(.01)

    def test_cancel_and_retry_preserve_matching_checkpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            started = threading.Event()
            def run(control):
                control.checkpoint('draft', {'cv': 'saved'})
                started.set()
                while not control.cancelled(): time.sleep(.01)
                return {'bad': 'must not publish'}
            jobs.start(root, 1, 2, run, {'language': 'en'}, revision='same')
            self.assertTrue(started.wait(2))
            jobs.cancel(root, 1, 2)
            for _ in range(200):
                state = jobs.status(root, 1, 2)
                if state['state'] == 'cancelled': break
                time.sleep(.01)
            self.assertEqual(state['state'], 'cancelled')
            self.assertNotIn('result', state)
            seen = []
            jobs.start(root, 1, 2, lambda control: seen.append(control.load('draft')) or {}, {'language': 'en'}, revision='same')
            for _ in range(200):
                if jobs.status(root, 1, 2)['state'] == 'completed': break
                time.sleep(.01)
            self.assertEqual(seen, [{'cv': 'saved'}])

    def test_draft_conflicts_and_delete(self):
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = jobs.save_draft(root, 1, 2, {'cv': 'first'}, None)
            with self.assertRaises(HTTPException) as error:
                jobs.save_draft(root, 1, 2, {'cv': 'stale'}, None)
            self.assertEqual(error.exception.status_code, 409)
            second = jobs.save_draft(root, 1, 2, {'cv': 'new'}, first['version'])
            self.assertEqual(jobs.load_draft(root, 1, 2)['draft']['cv'], 'new')
            jobs.delete(root, 1, 2)
            self.assertIsNone(jobs.load_draft(root, 1, 2))
            self.assertNotEqual(second['version'], first['version'])

    def test_deleting_profile_removes_all_its_application_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobs.save_draft(root, 7, 1, {'cv':'private'}, None)
            jobs.save_draft(root, 8, 1, {'cv':'other'}, None)
            jobs._write(root, 7, 2, {'id':'finished','state':'completed','updated_at':time.time()})
            jobs.delete_profile(root, 7)
            self.assertIsNone(jobs.load_draft(root, 7, 1))
            self.assertIsNone(jobs.status(root, 7, 2))
            self.assertIsNotNone(jobs.load_draft(root, 8, 1))

    def test_different_options_do_not_join_an_active_job(self):
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); started, finish = threading.Event(), threading.Event()
            def run(_): started.set(); finish.wait(5); return {}
            first = jobs.start(root, 1, 2, run, {'language':'en'}, revision='rev')
            self.assertTrue(started.wait(2))
            self.assertEqual(jobs.start(root, 1, 2, run, {'language':'en'}, revision='rev')['id'], first['id'])
            with self.assertRaises(HTTPException) as error:
                jobs.start(root, 1, 2, run, {'language':'de'}, revision='rev')
            self.assertEqual(error.exception.status_code, 409)
            finish.set()
            for _ in range(200):
                if jobs.status(root, 1, 2)['state'] == 'completed': break
                time.sleep(.01)
