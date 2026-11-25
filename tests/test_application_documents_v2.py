"""PostgreSQL checks for versioned application documents, the v2 queue and the render lane."""
from datetime import timedelta
import json
from pathlib import Path
import types
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import application_documents_v2 as documents, auto_preparation, codex_runtime, profiles
from hiring_scraper.app import application_preparation_queue as queue
from hiring_scraper.app.models import (
    ApplicationDocument, ApplicationDocumentRender, ApplicationPreparationTask, ApplicationRenderTask,
    Company, Job, JobFeed, ProfileCV, ProfilePhoto, UserProfile, utcnow,
)
from tests import pg
from tests.test_application_documents_v2_unit import letter

FIXTURE = Path(__file__).parent / 'fixtures' / 'documents' / 'flowing_en_cv.tex'
PASSED_PDF_CHECKS = [{'code': code, 'status': 'passed'} for code in (
    'text_extractable', 'glyphs', 'special_characters', 'name_present', 'contacts_present',
)]


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

    def test_cv_recreation_rejects_old_base_and_tailored_revisions(self):
        from hiring_scraper.app import source_changes

        def attach_cv():
            source = FIXTURE.read_text()
            with self.sessions.begin() as session:
                session.add(ProfileCV(profile_id=1, filename='cv.tex', content_type='application/x-tex', size_bytes=1,
                                      data=b'x', extracted_text=source, variants={'en': {'filename': 'cv.tex', 'text': source}}))
                source_changes.profile_changed(session, 1)

        attach_cv()
        self.save(0)
        with self.sessions() as session:
            initial = documents.latest_pair(session, 1, 2)['letter']
            base = json.loads(documents.get_cv_structure(1, 'en', session).body)
        with TemporaryDirectory() as folder, patch.object(codex_runtime, '_root', return_value=Path(folder)):
            with self.sessions() as session:
                profiles.delete_cv(1, session)
        attach_cv()
        self.save(0, paragraph='New fictional draft.')
        with self.sessions() as session, self.assertRaises(HTTPException) as conflict:
            documents.save_document(1, 'letter', 2,
                                    documents.SaveRequest(expected_version=1, expected_revision=initial['revision'],
                                                          body=initial['body']), session)
        self.assertEqual(conflict.exception.status_code, 409)
        with self.sessions() as session, self.assertRaises(HTTPException) as conflict:
            documents.revert_document(1, 'letter', 2,
                                      documents.RevertRequest(version=1, expected_version=1,
                                                              expected_revision=initial['revision']), session)
        self.assertEqual(conflict.exception.status_code, 409)
        with self.sessions() as session, self.assertRaises(HTTPException) as conflict:
            documents.put_cv_structure(1, documents.StructureRequest(
                document=base['document'], expected_revision=base['revision']), 'en', session)
        self.assertEqual(conflict.exception.status_code, 409)

    def test_publication_lock_waits_for_a_job_source_writer(self):
        from hiring_scraper.app.models import reserve_job_source_rows

        with self.sessions.begin() as writer:
            reserve_job_source_rows(writer, [2])
            with self.sessions() as publisher:
                publisher.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with self.assertRaises(OperationalError) as blocked:
                    documents._hold_publication(publisher, 1, 2)
                self.assertEqual(blocked.exception.orig.sqlstate, '55P03')

    def test_saved_documents_become_stale_after_job_changes(self):
        from hiring_scraper.app import source_changes
        from hiring_scraper.documents.import_tex import import_marked_tex

        source = FIXTURE.read_text()
        body = import_marked_tex(source, language='en').model_dump(mode='json')
        with self.sessions.begin() as session:
            session.add(ProfileCV(profile_id=1, filename='cv.tex', content_type='application/x-tex', size_bytes=1,
                                  data=b'x', extracted_text=source, variants={'en': {'document': body}}))
        with self.sessions.begin() as session:
            digest = documents._fresh_digest(session, 1, 2, 'en')
            self.assertIsNotNone(digest)
            documents.save_version(session, 1, 2, 'letter', letter(), expected_version=0,
                                   origin='ai', input_digest=digest)
            self.assertFalse(documents.latest_pair(session, 1, 2)['letter']['stale_inputs'])
        with self.sessions.begin() as session:
            session.get(Job, 2).description = 'The fictional role now involves operational reporting.'
            source_changes.jobs_changed(session, [2])
        with self.sessions() as session:
            self.assertTrue(documents.latest_pair(session, 1, 2)['letter']['stale_inputs'])
            self.assertTrue(documents.list_versions(session, 1, 2, 'letter')[0]['stale_inputs'])

    def test_cv_structure_imports_then_stores_projected_text_and_declares_the_change(self):
        source = FIXTURE.read_text()
        with self.sessions.begin() as session:
            session.add(ProfileCV(profile_id=1, filename='cv.tex', content_type='application/x-tex', size_bytes=1,
                                  data=b'x', extracted_text=source,
                                  variants={'en': {'filename': 'cv.tex', 'text': source}}))
        self.save(0)
        with self.sessions() as session:
            imported = documents.get_cv_structure(1, 'en', session)
        payload = json.loads(imported.body)
        self.assertEqual(payload['source'], 'imported')
        with self.sessions() as session:
            stored = json.loads(documents.put_cv_structure(
                1, documents.StructureRequest(document=payload['document'], expected_revision=payload['revision']), 'en', session).body)
        self.assertEqual(stored['source'], 'stored')
        with self.sessions() as session:
            variant = session.get(ProfileCV, 1).variants['en']
            self.assertEqual(variant['document']['language'], 'en')
            self.assertIn('radius:entry', variant['text'])
            self.assertTrue(documents.latest(session, 1, 2, 'letter').stale_inputs)

    def test_deleting_the_base_cv_removes_documents_render_tasks_and_pdfs(self):
        source = FIXTURE.read_text()
        with self.sessions.begin() as session:
            session.add(ProfileCV(profile_id=1, filename='cv.tex', content_type='application/x-tex',
                                  size_bytes=len(source), data=source.encode(), extracted_text=source,
                                  variants={'en': {'filename': 'cv.tex', 'text': source}}))
        self.save(0)
        self.save(1)
        with self.sessions.begin() as session:
            queue.enqueue_render(session, 1, 2, 'letter', 1, 'classic-serif-v1')
        self.assertTrue(queue.process_render_once(lambda *_: types.SimpleNamespace(
            pdf=b'%PDF-1.7', pages=1, checks=PASSED_PDF_CHECKS)))
        with self.sessions.begin() as session:
            queue.enqueue_render(session, 1, 2, 'letter', 2, 'classic-serif-v1')
        queue.enqueue_task(1, 2, 'prepare', {'job_id': 2, 'language': 'en'})

        with TemporaryDirectory() as folder, patch.object(codex_runtime, '_root', return_value=Path(folder)):
            jobs = Path(folder) / 'application-jobs'
            jobs.mkdir()
            legacy = jobs / '1-2.draft.json'
            legacy.write_text('{"draft":"Fictional legacy edit"}')
            with self.sessions() as session:
                self.assertEqual(profiles.delete_cv(1, session).status_code, 204)
            self.assertFalse(legacy.exists())
        with self.sessions() as session:
            for model in (ProfileCV, ApplicationDocument, ApplicationDocumentRender,
                          ApplicationRenderTask, ApplicationPreparationTask):
                self.assertIsNone(session.scalar(select(model).where(model.profile_id == 1)), model.__name__)

    def test_automatic_preparation_starts_only_after_the_base_cv_is_reviewed(self):
        source = FIXTURE.read_text()
        with self.sessions.begin() as session:
            session.add(ProfileCV(profile_id=1, filename='cv.tex', content_type='application/x-tex',
                                  size_bytes=len(source), data=source.encode(), extracted_text=source,
                                  variants={'en': {'filename': 'cv.tex', 'text': source}}))
        with self.sessions() as session:
            self.assertFalse(auto_preparation._queue_structured(
                session, session.get(UserProfile, 1), session.get(Job, 2), 'en', None))
            payload = json.loads(documents.get_cv_structure(1, 'en', session).body)
            documents.put_cv_structure(1, documents.StructureRequest(
                document=payload['document'], expected_revision=payload['revision']), 'en', session)
        with self.sessions() as session, patch.object(codex_runtime, 'selected_model', return_value=None):
            self.assertTrue(auto_preparation._queue_structured(
                session, session.get(UserProfile, 1), session.get(Job, 2), 'en', None))
        with self.sessions() as session:
            record = queue.status(session, 1, 2)
            self.assertEqual((record['kind'], record['automatic']), ('prepare', True))
            self.assertEqual(record['request']['mode'], 'full')

    def test_revision_returns_a_suggestion_without_modifying_the_document(self):
        from hiring_scraper.app import application_pipeline

        self.save(0)
        with patch.object(documents, 'snapshot_inputs', return_value=({}, 0)), \
                patch.object(documents, '_preparation_input'), \
                patch.object(application_pipeline, 'codex_generate'), \
                patch.object(application_pipeline, 'revise_unit', return_value={'text': 'I help studios ship.'}):
            with self.sessions() as session:
                base_revision = documents.latest(session, 1, 2, 'letter').revision
            outcome = documents.run_revise(1, {'job_id': 2, 'document': 'letter', 'unit_id': 'u-p1',
                'base_version': 1, 'base_revision': base_revision, 'instruction': 'Use more direct wording.'},
                Mock(cancelled=Mock(return_value=False)))
        self.assertFalse(outcome['applied'])
        self.assertEqual(outcome['original_text'], 'I build reliable services.')
        self.assertEqual(outcome['text'], 'I help studios ship.')
        with self.sessions() as session:
            self.assertEqual(documents.latest(session, 1, 2, 'letter').version, 1)

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

    def test_retry_retains_held_outputs_and_checkpoint(self):
        request = {'job_id': 2, 'language': 'en', 'mode': 'fast'}
        self.save(0)
        queue.enqueue_task(1, 2, 'prepare', request)
        key, token, record = queue._claim()
        progress = queue.Progress(key, token, {}, task_id=record['id'], record=record)
        checkpoint = {'fictional': 'checkpoint'}
        body = letter(paragraph='Generated fictional draft.')
        documents._publish_stage(1, 2, progress, 'digest', request, 'letter:digest', checkpoint,
                                 [('letter', body, [])])
        progress('review')
        progress._update(finish=True, state='failed', stage='failed')
        with self.sessions() as session:
            partial = queue.status(session, 1, 2)
        self.assertEqual((partial['state'], partial['failed_stage']), ('partial', 'review'))
        retried = queue.enqueue_task(1, 2, 'prepare', request)
        self.assertEqual(retried['checkpoints']['letter:digest'], checkpoint)
        self.assertEqual(retried['held']['letter']['body']['paragraphs'][0]['text'], 'Generated fictional draft.')
        self.assertNotEqual(retried['id'], record['id'])

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
        with self.sessions() as session:
            base = documents._base_revisions(session, 1, 2)
        queue.enqueue_task(1, 2, 'prepare', {'job_id': 2, 'language': 'en'},
                           extra={'base_revisions': base})
        key, token, record = queue._claim()
        progress = queue.Progress(key, token, {}, task_id=record['id'], record=record)
        with patch.object(documents, '_fresh_digest', return_value='digest'):
            documents._publish_stage(1, 2, progress, 'digest', {'language': 'en'}, 'draft', None,
                                     [('letter', letter(paragraph='AI one.'), [])])
            self.save(2, paragraph='User edit.')  # the user saves while the review stage runs
            documents._publish_stage(1, 2, progress, 'digest', {'language': 'en'}, 'review', None,
                                     [('letter', letter(paragraph='AI reviewed.'), [])])
        with self.sessions() as session:
            current = documents.latest(session, 1, 2, 'letter')
            record = queue.status(session, 1, 2)
        self.assertEqual((current.version, current.origin), (3, 'user'))
        self.assertEqual(record['published'], {'letter': 2})
        self.assertEqual(record['held']['letter']['body']['paragraphs'][0]['text'], 'AI reviewed.')

    # --- render lane -------------------------------------------------------------

    def test_removing_portrait_purges_cached_cv_and_fences_a_claimed_render(self):
        from hiring_scraper.documents.import_tex import import_marked_tex

        body = import_marked_tex(FIXTURE.read_text(), language='en').model_dump(mode='json')
        body['photo'] = True
        portrait = b'fictional portrait data'
        with self.sessions.begin() as session:
            session.add(ProfilePhoto(profile_id=1, filename='fixture.png', size_bytes=len(portrait), data=portrait))
            documents.save_version(session, 1, 2, 'cv', body, expected_version=0, origin='user')
            documents.save_version(session, 1, 2, 'cv', body, expected_version=1, origin='user')
            session.flush()
            key = documents.render_key(session, 1, 'cv', body)
            queue.enqueue_render(session, 1, 2, 'cv', 1, key)
            queue.enqueue_render(session, 1, 2, 'cv', 2, key)
        result = types.SimpleNamespace(pdf=b'fictional PDF with portrait', pages=1, checks=PASSED_PDF_CHECKS)
        self.assertTrue(queue.process_render_once(lambda *_args: result))
        claimed_key, token, *_ = queue._claim_render()
        with self.sessions() as session:
            profiles.delete_photo(1, session)
        self.assertFalse(queue._finish_render(claimed_key, token, result))
        with self.sessions() as session:
            self.assertEqual(session.scalars(select(ApplicationDocumentRender)).all(), [])
            self.assertEqual(session.scalars(select(ApplicationRenderTask)).all(), [])
            self.assertNotEqual(documents.render_key(session, 1, 'cv', body), key)

    def test_pdf_that_fails_verification_is_not_cached_or_downloaded(self):
        self.save(0)
        with self.sessions() as session:
            saved = documents.latest_pair(session, 1, 2)['letter']
            response = documents.document_pdf(1, 'letter', 2, saved['version'],
                                              revision=saved['revision'], session=session)
            self.assertEqual(response.status_code, 202)
        result = types.SimpleNamespace(pdf=b'fictional blank PDF', pages=1,
                                       checks=[{'code': 'text_extractable', 'status': 'failed'}])
        self.assertTrue(queue.process_render_once(lambda *_: result))
        with self.sessions() as session:
            self.assertEqual(session.scalars(select(ApplicationDocumentRender)).all(), [])
            response = documents.document_pdf(1, 'letter', 2, saved['version'],
                                              revision=saved['revision'], session=session)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(json.loads(response.body)['error_code'], 'render_verification_failed')

    def test_render_lane_caches_pdfs_and_stops_after_failures(self):
        self.save(0)
        with self.sessions.begin() as session:
            self.assertEqual(queue.enqueue_render(session, 1, 2, 'letter', 1, 'classic-serif-v1')['state'], 'queued')
        result = types.SimpleNamespace(pdf=b'%PDF-1.7', pages=1, checks=PASSED_PDF_CHECKS)
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
