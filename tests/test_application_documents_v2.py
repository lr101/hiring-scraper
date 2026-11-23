"""PostgreSQL checks for versioned application documents, the v2 queue and the render lane."""
from datetime import timedelta
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import application_documents_v2 as documents
from hiring_scraper.app import application_preparation_queue as queue
from hiring_scraper.app.models import (
    ApplicationDocument, ApplicationDocumentRender, ApplicationPreparationTask, ApplicationRenderTask,
    Company, Job, JobFeed, ProfileCV, UserProfile, utcnow,
)
from tests import pg
from tests.test_application_documents_v2_unit import letter

FIXTURE = Path(__file__).parent / 'fixtures' / 'documents' / 'flowing_en_cv.tex'


class ApplicationDocumentsV2Tests(unittest.TestCase):
    def setUp(self):
        engine = pg.engine(self)
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(UserProfile(id=1, name='Documents'))
            feed = JobFeed(company=Company(source='test', source_id='docs', name='Employer'),
                           provider='rss', feed_url='https://example.test/jobs.xml', status='parsed')
            session.add(feed)
            session.flush()
            session.add_all([Job(id=job_id, feed_id=feed.id, external_id=str(job_id), title='Role',
                                 url=f'https://example.test/{job_id}') for job_id in (2, 3)])
        for module in (queue, documents):
            patcher = patch.object(module, 'SessionLocal', self.sessions)
            patcher.start()
            self.addCleanup(patcher.stop)

    def save(self, expected, origin='user', **changes):
        with self.sessions.begin() as session:
            return documents.save_version(session, 1, 2, 'letter', letter(**changes),
                                          expected_version=expected, origin=origin)

    # --- storage ---------------------------------------------------------------

    def test_compare_and_set_versions_conflict_and_prune_to_twenty(self):
        self.assertEqual(self.save(0), 1)
        with self.assertRaises(documents.VersionConflict):
            self.save(0)
        for expected in range(1, 25):
            self.save(expected)
        with self.sessions() as session:
            versions = [row['version'] for row in documents.list_versions(session, 1, 2, 'letter')]
        self.assertEqual(versions, list(range(25, 5, -1)))
        with self.assertRaises(documents.VersionConflict):
            self.save(3)  # pruned and stale: the max check still rejects it

    def test_revert_copies_an_old_version_and_job_deletion_cascades(self):
        self.save(0, paragraph='First draft.')
        self.save(1, paragraph='Second draft.')
        with self.sessions.begin() as session:
            self.assertEqual(documents.revert(session, 1, 2, 'letter', 1), 3)
        with self.sessions() as session:
            current = documents.latest_pair(session, 1, 2)['letter']
        self.assertEqual(current['body']['paragraphs'][0]['text'], 'First draft.')
        self.assertEqual(current['origin'], 'user')
        with self.sessions.begin() as session:
            session.delete(session.get(Job, 2))
        with self.sessions() as session:
            self.assertEqual(session.scalars(select(ApplicationDocument)).all(), [])

    def test_cv_structure_imports_then_stores_projected_text_and_declares_the_change(self):
        source = FIXTURE.read_text()
        with self.sessions.begin() as session:
            session.add(ProfileCV(profile_id=1, filename='cv.tex', content_type='application/x-tex', size_bytes=1,
                                  data=b'x', extracted_text=source,
                                  variants={'en': {'filename': 'cv.tex', 'text': source}}))
        self.save(0)
        with self.sessions() as session:
            imported = documents.get_cv_structure(1, 'en', session)
        import json
        payload = json.loads(imported.body)
        self.assertEqual(payload['source'], 'imported')
        with self.sessions() as session:
            stored = json.loads(documents.put_cv_structure(
                1, documents.StructureRequest(document=payload['document']), 'en', session).body)
        self.assertEqual(stored['source'], 'stored')
        with self.sessions() as session:
            variant = session.get(ProfileCV, 1).variants['en']
            self.assertEqual(variant['document']['language'], 'en')
            self.assertIn('radius:entry', variant['text'])
            self.assertTrue(documents.latest(session, 1, 2, 'letter').stale_inputs)

    # --- queue -----------------------------------------------------------------

    def test_one_active_task_per_job_and_retry_waits_for_next_attempt(self):
        task = queue.enqueue_task(1, 2, 'prepare', {'job_id': 2, 'language': 'en'})
        self.assertEqual(queue.enqueue_task(1, 2, 'prepare', {'job_id': 2, 'language': 'en'})['id'], task['id'])
        with self.assertRaises(queue.HTTPException):
            queue.enqueue_task(1, 2, 'revise', {'job_id': 2})

        def busy(*_args):
            raise queue.TaskError('busy', 'Busy.', retryable=True)

        self.assertTrue(queue.process_once(busy))
        with self.sessions() as session:
            record = queue.status(session, 1, 2)
            self.assertEqual((record['state'], record['stage'], record['attempts']), ('running', 'retry_wait', 1))
        self.assertFalse(queue.process_once(busy))  # not claimable before next_attempt_at
        for attempt in (2, 3):
            with self.sessions.begin() as session:
                session.get(ApplicationPreparationTask, (1, 2)).next_attempt_at = utcnow() - timedelta(seconds=1)
            self.assertTrue(queue.process_once(busy))
        with self.sessions() as session:
            record = queue.status(session, 1, 2)
        self.assertEqual((record['state'], record['error_code'], record['attempts']), ('failed', 'busy', 3))

    def test_expired_owner_cannot_publish_or_renew(self):
        queue.enqueue_task(1, 2, 'prepare', {'job_id': 2, 'language': 'en'})
        key, token, record = queue._claim()
        stale = queue.Progress(key, token, {}, task_id=record['id'], record=record)
        with self.sessions.begin() as session:
            session.get(ApplicationPreparationTask, key).lease_until = utcnow() - timedelta(seconds=1)
        replacement = queue._claim()
        self.assertIsNotNone(replacement)
        self.assertFalse(stale._renew())
        with self.sessions.begin() as session:
            self.assertIsNone(stale.fenced(session))

    def test_ai_output_is_held_when_the_user_saved_during_generation(self):
        self.save(0)
        queue.enqueue_task(1, 2, 'prepare', {'job_id': 2, 'language': 'en'},
                           extra={'base_versions': {'cv': 0, 'letter': 1}})
        key, token, record = queue._claim()
        progress = queue.Progress(key, token, {}, task_id=record['id'], record=record)
        fake = types.SimpleNamespace(input_digest=lambda _inp: 'digest', PreparationInput=lambda **kw: kw)
        with patch.dict(sys.modules, {'hiring_scraper.app.application_pipeline': fake}), \
                patch.object(documents, 'snapshot_inputs', return_value=({}, 0)):
            documents._publish_prepared(1, 2, progress, 'digest', {'language': 'en'}, 'letter',
                                        letter(paragraph='AI one.'), [])
            self.save(2, paragraph='User edit.')  # the user saves while the review stage runs
            documents._publish_prepared(1, 2, progress, 'digest', {'language': 'en'}, 'letter',
                                        letter(paragraph='AI reviewed.'), [])
        with self.sessions() as session:
            current = documents.latest(session, 1, 2, 'letter')
            record = queue.status(session, 1, 2)
        self.assertEqual((current.version, current.origin), (3, 'user'))
        self.assertEqual(record['published'], {'letter': 2})
        self.assertEqual(record['held']['letter']['body']['paragraphs'][0]['text'], 'AI reviewed.')

    # --- render lane -------------------------------------------------------------

    def test_render_lane_caches_pdfs_and_stops_after_failures(self):
        self.save(0)
        with self.sessions.begin() as session:
            self.assertEqual(queue.enqueue_render(session, 1, 2, 'letter', 1, 'classic-serif-v1')['state'], 'queued')
        result = types.SimpleNamespace(pdf=b'%PDF-1.7', pages=1, checks=[])
        self.assertTrue(queue.process_render_once(lambda *_args: result))
        with self.sessions() as session:
            self.assertEqual(documents.get_render(session, 1, 2, 'letter', 1, 'classic-serif-v1').pdf, b'%PDF-1.7')
            self.assertIsNone(session.get(ApplicationRenderTask, (1, 2, 'letter', 1, 'classic-serif-v1')))
        self.save(1)
        with self.sessions.begin() as session:
            queue.enqueue_render(session, 1, 2, 'letter', 2, 'classic-serif-v1')

        def broken(*_args):
            raise queue.TaskError('render_failed', 'Broken.', retryable=True)

        for _ in range(3):
            with self.sessions.begin() as session:
                row = session.get(ApplicationRenderTask, (1, 2, 'letter', 2, 'classic-serif-v1'))
                if row.available_at is not None:
                    row.available_at = utcnow() - timedelta(seconds=1)
            queue.process_render_once(broken)
        with self.sessions() as session:
            row = session.get(ApplicationRenderTask, (1, 2, 'letter', 2, 'classic-serif-v1'))
            self.assertEqual((row.state, row.attempts, row.error_code), ('failed', 3, 'render_failed'))
            self.assertEqual(session.scalars(select(ApplicationDocumentRender)).all()[0].version, 1)


if __name__ == '__main__':
    unittest.main()
