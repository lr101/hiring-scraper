"""Database-free checks for application documents v2 storage math, queue policy and API validation."""
import asyncio
from datetime import timedelta
import json
import sys
import types
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import OperationalError

from hiring_scraper.app import application_documents_v2 as documents
from hiring_scraper.app import application_preparation_queue as queue
from hiring_scraper.app.models import ProfileIndexChanged


def letter(paragraph='I build reliable services.', closing='Kind regards'):
    return {
        'format': 'radius-letter-v2', 'language': 'en', 'template': 'classic-serif-v1', 'page': 'a4',
        'sender': {'name': 'Alex Example', 'lines': ['Example Street 1']},
        'recipient_lines': ['Employer GmbH'], 'place_date': 'Bremen, 1 May 2026',
        'subject': {'id': 'u-subject', 'text': 'Application', 'lead_in': '', 'origin': 'ai', 'sources': [],
                    'role': '', 'hidden': False},
        'salutation': {'id': 'u-salutation', 'text': 'Dear team,', 'lead_in': '', 'origin': 'ai', 'sources': [],
                       'role': '', 'hidden': False},
        'paragraphs': [{'id': 'u-p1', 'text': paragraph, 'lead_in': '', 'origin': 'ai', 'sources': ['job'],
                        'role': 'motivation', 'hidden': False}],
        'closing': {'id': 'u-closing', 'text': closing, 'lead_in': '', 'origin': 'ai', 'sources': [],
                    'role': '', 'hidden': False},
        'signature': 'Alex Example',
    }


class VersionMathTests(unittest.TestCase):
    def test_next_version_is_expected_plus_one_and_rejects_invalid_expectations(self):
        self.assertEqual(documents.next_version(0), 1)
        self.assertEqual(documents.next_version(19), 20)
        for invalid in (-1, True, 1.0, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                documents.next_version(invalid)

    def test_prune_keeps_the_newest_twenty_versions(self):
        self.assertEqual(documents.KEEP_VERSIONS, 20)
        self.assertEqual(documents.prune_floor(20), 0)
        self.assertEqual(documents.prune_floor(21), 1)
        kept = [version for version in range(1, 31) if version > documents.prune_floor(30)]
        self.assertEqual(kept, list(range(11, 31)))

    def test_ai_output_replaces_only_versions_the_task_started_from_or_published(self):
        self.assertTrue(documents.should_publish(None, 0, None))
        self.assertTrue(documents.should_publish(3, 3, None))  # unchanged since the task started
        self.assertTrue(documents.should_publish(5, 3, 5))  # the task's own CV stage output
        self.assertFalse(documents.should_publish(4, 3, None))  # the user saved meanwhile
        self.assertFalse(documents.should_publish(6, 3, 5))

    def test_validate_body_canonicalizes_and_rejects_unknown_fields(self):
        body = documents.validate_body('letter', letter())
        self.assertEqual(body['format'], 'radius-letter-v2')
        with self.assertRaises(Exception):
            documents.validate_body('letter', {**letter(), 'latex': '\\input{x}'})
        with self.assertRaises(ValueError):
            documents.validate_body('memo', letter())

    def test_user_save_keeps_findings_only_for_unchanged_units(self):
        findings = [{'id': 'f-1', 'unit_id': 'u-p1', 'code': 'unsupported_number'},
                    {'id': 'f-2', 'unit_id': 'u-closing', 'code': 'placeholder'},
                    {'id': 'f-3', 'unit_id': None, 'code': 'extra_page'}]
        kept = documents.carry_findings('letter', letter(), findings, letter(paragraph='I rewrote this.'))
        self.assertEqual([finding['id'] for finding in kept], ['f-2', 'f-3'])

    def test_public_task_hides_checkpoints_and_requests(self):
        record = {'id': 'a', 'kind': 'prepare', 'state': 'running', 'stage': 'cv', 'attempts': 1,
                  'checkpoints': {'cv:x': {'secret': 1}}, 'request': {'job_id': 1, 'language': 'de', 'mode': 'fast', 'model': 'private-model'}, 'held': {}}
        task = documents.public_task(record)
        self.assertNotIn('checkpoints', task)
        self.assertNotIn('request', task)
        self.assertEqual(task['preparation'], {'language': 'de', 'mode': 'fast'})
        self.assertEqual(task['stages'], {})
        self.assertEqual(documents.public_task({'id': 'b', 'state': 'running'})['kind'], 'legacy')

    def test_localized_preferences_apply_the_language_translation(self):
        preferences = {'summary': 'English', 'localized_content': {'de': {'summary': 'Deutsch',
                       'application_preferences': {'start': 'sofort'}}}}
        localized = documents.localized_preferences(preferences, 'de')
        self.assertEqual(localized['summary'], 'Deutsch')
        self.assertEqual(localized['application_details']['preferences'], {'start': 'sofort'})
        self.assertEqual(documents.localized_preferences(preferences, 'en')['summary'], 'English')

    def test_pipeline_switch_defaults_to_v2(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(documents.pipeline_version(), 'v2')
        with patch.dict('os.environ', {'RADIUS_APPLICATION_PIPELINE': 'V1'}):
            self.assertEqual(documents.pipeline_version(), 'v1')


class QueuePolicyTests(unittest.TestCase):
    def test_backoff_is_two_then_ten_minutes_for_three_attempts(self):
        self.assertEqual(queue.MAX_ATTEMPTS, 3)
        self.assertEqual(queue.retry_delay(1), timedelta(minutes=2))
        self.assertEqual(queue.retry_delay(2), timedelta(minutes=10))
        self.assertIsNone(queue.retry_delay(3))
        self.assertIsNone(queue.retry_delay(0))
        self.assertEqual(queue.LEASE, timedelta(minutes=6))

    def test_renewal_clock_allows_one_renewal_per_interval(self):
        now = [100.0]
        clock = queue.RenewalClock(60, clock=lambda: now[0])
        self.assertFalse(clock.due())
        now[0] = 159.9
        self.assertFalse(clock.due())
        now[0] = 160.0
        self.assertTrue(clock.due())
        self.assertFalse(clock.due())
        now[0] = 230.0
        clock.reset()
        now[0] = 289.0
        self.assertFalse(clock.due())

    def test_cancelled_renews_the_lease_at_most_every_sixty_seconds(self):
        now = [0.0]
        progress = queue.Progress((1, 2), 'token', {}, task_id='t', record={'kind': 'prepare'},
                                  clock=lambda: now[0])
        renewals, reads = [], []

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def get(self, *_args):
                reads.append(1)
                return types.SimpleNamespace(lease_token='token', state='running', record={'id': 't'})

        with patch.object(queue.Progress, '_renew', lambda self: renewals.append(now[0]) or True), \
                patch.object(queue, 'SessionLocal', Session):
            for second in range(0, 181, 10):
                now[0] = float(second)
                self.assertFalse(progress.cancelled())
        self.assertEqual(renewals, [60.0, 120.0, 180.0])
        self.assertEqual(len(reads), 19 - 3)

    def test_fencing_requires_both_token_and_task_id(self):
        progress = queue.Progress((1, 2), 'token', {}, task_id='t')
        row = types.SimpleNamespace(lease_token='token', record={'id': 't'})
        self.assertTrue(progress._owns(row))
        self.assertFalse(progress._owns(types.SimpleNamespace(lease_token='token', record={'id': 'other'})))
        self.assertFalse(progress._owns(types.SimpleNamespace(lease_token='other', record={'id': 't'})))
        self.assertFalse(progress._owns(None))

    def test_classify_errors_without_importing_pipeline_modules(self):
        self.assertEqual(queue.classify(queue.TaskError('job_closed', 'Closed.')), ('job_closed', False, 'Closed.'))
        self.assertEqual(queue.classify(ProfileIndexChanged())[:2], ('database_contention', True))
        transient = OperationalError('SELECT 1', {}, Exception('deadlock detected'))
        with patch.object(queue, 'is_transient_database_error', return_value=True):
            self.assertEqual(queue.classify(transient)[:2], ('database_contention', True))
        self.assertEqual(queue.classify(RuntimeError('x'))[:2], ('unexpected', False))

        class GenerationError(Exception):
            def __init__(self, code, retryable):
                super().__init__(code)
                self.code, self.retryable = code, retryable

        class RenderFailed(Exception):
            def __init__(self, code):
                super().__init__(code)
                self.code = code

        pipeline = types.ModuleType('hiring_scraper.app.application_pipeline')
        pipeline.GenerationError = GenerationError
        render = types.ModuleType('hiring_scraper.app.application_pdf')
        render.RenderFailed, render.RenderUnavailable = RenderFailed, type('RenderUnavailable', (Exception,), {})
        with patch.dict(sys.modules, {pipeline.__name__: pipeline, render.__name__: render}):
            self.assertEqual(queue.classify(GenerationError('timeout', True))[:2], ('timeout', True))
            self.assertEqual(queue.classify(GenerationError('refused', False))[:2], ('refused', False))
            self.assertEqual(queue.classify(RenderFailed('crash'))[:2], ('crash', True))
            self.assertEqual(queue.classify(render.RenderUnavailable())[:2], ('render_unavailable', False))

    def run_failing(self, error, record):
        updates = []
        with patch.object(queue, '_claim', return_value=((1, 2), 'token', record)), \
                patch.object(queue.Progress, '_update', lambda self, **changes: updates.append(changes) or True):
            self.assertTrue(queue.process_once(lambda *_args: (_ for _ in ()).throw(error)))
        return updates[-1]

    def test_transient_v2_failures_wait_for_backoff_then_fail_with_a_code(self):
        transient = queue.TaskError('busy', 'Busy.', retryable=True)
        first = self.run_failing(transient, {'id': 't', 'kind': 'prepare', 'attempts': 1})
        self.assertEqual((first['state'], first['stage'], first['error_code']), ('running', 'retry_wait', 'busy'))
        self.assertTrue(first['finish'])
        self.assertIsNotNone(first['retry_at'])
        last = self.run_failing(transient, {'id': 't', 'kind': 'prepare', 'attempts': 3})
        self.assertEqual((last['state'], last['error_code']), ('failed', 'busy'))
        permanent = self.run_failing(queue.TaskError('job_closed', 'Closed.'), {'id': 't', 'kind': 'revise', 'attempts': 1})
        self.assertEqual((permanent['state'], permanent['error_code']), ('failed', 'job_closed'))

    def test_legacy_tasks_never_retry_automatically(self):
        legacy = self.run_failing(queue.TaskError('busy', 'Busy.', retryable=True), {'id': 't', 'attempts': 1})
        self.assertEqual(legacy['state'], 'failed')
        http = self.run_failing(HTTPException(502, 'Bad AI output.'), {'id': 't', 'attempts': 1})
        self.assertEqual((http['state'], http['error'], http['error_code']), ('failed', 'Bad AI output.', 'http_502'))


class FakeSession:
    """Just enough of a Session for routes that fail validation before touching storage."""

    def get(self, *_args, **_kwargs):
        return types.SimpleNamespace(id=1)

    def rollback(self):
        pass

    def close(self):
        pass


def call(method, path, body=None, query=b''):
    app = FastAPI()
    app.include_router(documents.router)
    from hiring_scraper.app.database import get_session
    app.dependency_overrides[get_session] = lambda: FakeSession()
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': json.dumps(body).encode() if body is not None else b''}

    async def send(message):
        messages.append(message)

    asyncio.run(app({'type': 'http', 'http_version': '1.1', 'method': method, 'scheme': 'http', 'path': path,
                     'query_string': query, 'headers': [(b'content-type', b'application/json')],
                     'server': ('test', 80), 'client': ('test', 1), 'root_path': ''}, receive, send))
    start = next(message for message in messages if message['type'] == 'http.response.start')
    payload = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
    return start['status'], json.loads(payload or b'null'), dict(start['headers'])


class RouterValidationTests(unittest.TestCase):
    def test_save_rejects_unknown_kinds_invalid_bodies_and_extra_fields(self):
        status, _, _ = call('PUT', '/api/v1/profiles/1/application-documents/memo', {'expected_version': 0, 'body': {}},
                            b'job_id=1')
        self.assertEqual(status, 422)
        status, payload, _ = call('PUT', '/api/v1/profiles/1/application-documents/letter',
                                  {'expected_version': 0, 'body': {'format': 'radius-letter-v2'}}, b'job_id=1')
        self.assertEqual(status, 422)
        self.assertIn('letter document is invalid', payload['detail'])
        status, _, _ = call('PUT', '/api/v1/profiles/1/application-documents/letter',
                            {'expected_version': -1, 'body': letter()}, b'job_id=1')
        self.assertEqual(status, 422)
        status, _, _ = call('PUT', '/api/v1/profiles/1/application-documents/letter',
                            {'expected_version': 0, 'body': letter(), 'origin': 'ai'}, b'job_id=1')
        self.assertEqual(status, 422)

    def test_save_conflict_is_409_and_responses_are_not_cached(self):
        with patch.object(documents, 'save_version', side_effect=documents.VersionConflict(3)), \
                patch.object(documents, 'get_version', return_value=None):
            status, _, headers = call('PUT', '/api/v1/profiles/1/application-documents/letter',
                                      {'expected_version': 2, 'body': letter()}, b'job_id=1')
        self.assertEqual(status, 409)
        self.assertEqual(headers.get(b'cache-control'), b'no-store')
        with patch.object(documents, 'save_version', return_value=1), \
                patch.object(FakeSession, 'commit', lambda self: None, create=True):
            status, payload, headers = call('PUT', '/api/v1/profiles/1/application-documents/letter',
                                            {'expected_version': 0, 'body': letter()}, b'job_id=1')
        self.assertEqual((status, payload), (200, {'version': 1}))
        self.assertEqual(headers.get(b'cache-control'), b'no-store')

    def test_prepare_and_revise_validate_requests(self):
        status, _, _ = call('POST', '/api/v1/profiles/1/application-documents/prepare',
                            {'job_id': 1, 'language': 'fr'})
        self.assertEqual(status, 422)
        status, _, _ = call('POST', '/api/v1/profiles/1/application-documents/prepare',
                            {'job_id': 1, 'mode': 'turbo'})
        self.assertEqual(status, 422)
        status, _, _ = call('POST', '/api/v1/profiles/1/application-documents/letter/revise',
                            {'unit_id': 'U P1', 'instruction': 'Shorter'}, b'job_id=1')
        self.assertEqual(status, 422)
        status, _, _ = call('POST', '/api/v1/profiles/1/application-documents/letter/revise',
                            {'unit_id': 'u-p1', 'instruction': 'x' * 501}, b'job_id=1')
        self.assertEqual(status, 422)

    def test_prepare_is_refused_when_the_v1_pipeline_is_selected(self):
        with patch.dict('os.environ', {'RADIUS_APPLICATION_PIPELINE': 'v1'}):
            status, payload, _ = call('POST', '/api/v1/profiles/1/application-documents/prepare', {'job_id': 1})
        self.assertEqual(status, 409)
        self.assertIn('turned off', payload['detail'])

    def test_cv_structure_rejects_invalid_documents_before_storage(self):
        status, _, _ = call('PUT', '/api/v1/profiles/1/cv/structure', {'document': {'format': 'radius-cv-v2'}},
                            b'language=en')
        self.assertEqual(status, 422)


if __name__ == '__main__':
    unittest.main()
