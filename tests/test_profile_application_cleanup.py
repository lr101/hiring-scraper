"""Profile deletion must serialize with writes to private application state."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Response

from hiring_scraper.app import (
    application_generation as generation,
    application_generation_jobs as jobs,
    codex_runtime,
    document_revision as revision,
    profiles,
)


class ProfileApplicationCleanupTests(unittest.TestCase):
    def test_profile_deletion_waits_for_a_validated_preparation_to_be_saved(self):
        self.assert_deletion_serializes_with_preparation(profiles.delete_profile)

    def test_cv_deletion_waits_for_a_validated_preparation_to_be_saved(self):
        self.assert_deletion_serializes_with_preparation(profiles.delete_cv)

    def test_profile_deletion_waits_for_a_validated_revision_to_be_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot_started = threading.Event()
            release_snapshot = threading.Event()
            deletion_committed = threading.Event()
            start_errors = []
            delete_errors = []

            class Session:
                def rollback(self):
                    pass

                def delete(self, _profile):
                    pass

                def commit(self):
                    deletion_committed.set()

            session = Session()
            request = revision.DocumentRevisionRequest(
                job_id=42,
                cv_updated_at='cv-revision',
                profile_updated_at='profile-revision',
                language='en',
                target='cover_letter',
                instruction='Make it direct.',
                current_document='Private current document text.',
            )

            def snapshot(*_args):
                snapshot_started.set()
                if not release_snapshot.wait(3):
                    raise RuntimeError('Timed out waiting to finish the snapshot.')
                return {'original_cv_text': 'Original CV text.'}, 'revision'

            def save_job(root_path, profile_id, job_id, _runner, saved_request, revision):
                record = {
                    'id': 'revision',
                    'state': 'running',
                    'updated_at': time.time(),
                    'request': saved_request,
                    'revision': revision,
                }
                jobs._write(root_path, profile_id, job_id, record)
                return record

            def start_revision():
                try:
                    with (
                        patch.object(revision, 'generation_status', return_value={'enabled': True}),
                        patch.object(revision, '_snapshot', side_effect=snapshot),
                        patch.object(revision, '_validate_document'),
                        patch.object(jobs, 'start', side_effect=save_job),
                        patch.object(codex_runtime, '_root', return_value=root),
                    ):
                        revision.start_revision(7, request, Response(), session)
                except Exception as error:
                    start_errors.append(error)

            def delete_profile():
                try:
                    with (
                        patch.object(profiles, 'require_profile', return_value=SimpleNamespace(cv='attached')),
                        patch.object(codex_runtime, '_root', return_value=root),
                    ):
                        profiles.delete_profile(7, session)
                except Exception as error:
                    delete_errors.append(error)

            starter = threading.Thread(target=start_revision, daemon=True)
            deleter = threading.Thread(target=delete_profile, daemon=True)
            starter.start()
            self.assertTrue(snapshot_started.wait(2), 'Revision did not reach its validated snapshot.')
            deleter.start()
            try:
                self.assertFalse(
                    deletion_committed.wait(.2),
                    'Profile deletion must wait until the revision request is visible to cleanup.',
                )
            finally:
                release_snapshot.set()
                starter.join(5)
                deleter.join(5)

            self.assertFalse(starter.is_alive(), 'Revision request did not finish.')
            self.assertFalse(deleter.is_alive(), 'Profile deletion did not finish.')
            self.assertEqual(start_errors, [])
            self.assertEqual(delete_errors, [])
            self.assertTrue(deletion_committed.is_set())
            self.assertIsNone(jobs.status(root / 'document-revisions', 7, 42))

    def assert_deletion_serializes_with_preparation(self, delete_route):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot_started = threading.Event()
            release_snapshot = threading.Event()
            deletion_committed = threading.Event()
            start_errors = []
            delete_errors = []

            class Session:
                def rollback(self):
                    pass

                def delete(self, _profile):
                    pass

                def commit(self):
                    deletion_committed.set()

            session = Session()
            request = generation.PrepareApplicationRequest(
                job_id=42,
                cv_updated_at='cv-revision',
                profile_updated_at='profile-revision',
                language='en',
                notes='private application instructions',
            )

            def snapshot(*_args):
                snapshot_started.set()
                if not release_snapshot.wait(3):
                    raise RuntimeError('Timed out waiting to finish the snapshot.')
                return {}, 'revision'

            def save_job(root_path, profile_id, job_id, _runner, saved_request, revision):
                record = {
                    'id': 'prepared',
                    'state': 'running',
                    'updated_at': time.time(),
                    'request': saved_request,
                    'revision': revision,
                }
                jobs._write(root_path, profile_id, job_id, record)
                return record

            def start_preparation():
                try:
                    with (
                        patch.object(generation, 'generation_status', return_value={'enabled': True}),
                        patch.object(generation, '_snapshot', side_effect=snapshot),
                        patch.object(jobs, 'start', side_effect=save_job),
                        patch.object(codex_runtime, '_root', return_value=root),
                    ):
                        generation.start_preparation_job(7, request, Response(), session)
                except Exception as error:
                    start_errors.append(error)

            def delete_profile():
                try:
                    with (
                        patch.object(profiles, 'require_profile', return_value=SimpleNamespace(cv='attached')),
                        patch.object(codex_runtime, '_root', return_value=root),
                    ):
                        delete_route(7, session)
                except Exception as error:
                    delete_errors.append(error)

            starter = threading.Thread(target=start_preparation, daemon=True)
            deleter = threading.Thread(target=delete_profile, daemon=True)
            starter.start()
            self.assertTrue(snapshot_started.wait(2), 'Preparation did not reach its validated snapshot.')
            deleter.start()
            try:
                self.assertFalse(
                    deletion_committed.wait(.2),
                    f'{delete_route.__name__} must wait until the validated preparation record is visible to cleanup.',
                )
            finally:
                release_snapshot.set()
                starter.join(5)
                deleter.join(5)

            self.assertFalse(starter.is_alive(), 'Preparation request did not finish.')
            self.assertFalse(deleter.is_alive(), 'Profile deletion did not finish.')
            self.assertEqual(start_errors, [])
            self.assertEqual(delete_errors, [])
            self.assertTrue(deletion_committed.is_set())
            self.assertIsNone(jobs.status(root, 7, 42))
