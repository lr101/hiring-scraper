"""Direct preparation must preserve source facts and reject unusable AI output."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from codex_fixture import write_codex_fixture

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from hiring_scraper.app import models, profiles
from hiring_scraper.app.latex_documents import latex_source


class ApplicationGenerationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        profile = profiles.create_profile(profiles.ProfileRequest(name='Alex', summary='Product coordination',
            language_levels={'German': 'B2'}, custom_instructions='Keep my direct voice.'), self.session)
        self.profile_id = profile['id']
        company = models.Company(source='test', source_id='generation', name='Example GmbH')
        feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.invalid/jobs')
        job = models.Job(feed=feed, external_id='42', title='Product coordinator',
                         description='Coordinate product requirements.', url='https://example.invalid/jobs/42')
        cv = models.ProfileCV(profile_id=self.profile_id, filename='original.tex', content_type='application/x-tex',
                              size_bytes=100, data=b'original', extracted_text=latex_source('Alex Example'))
        self.session.add_all([job, cv]); self.session.commit()
        self.job_id = job.id
        self.revision = profiles.cv_metadata(cv)['updated_at']
        self.profile_revision = profiles.profile_json(self.session.get(models.UserProfile, self.profile_id))['updated_at']
        self.runtime = tempfile.TemporaryDirectory()
        root = Path(self.runtime.name)
        executable = write_codex_fixture(root)
        (root/'home').mkdir(); (root/'home/auth.json').write_text('{"auth_mode":"chatgpt"}')
        self.env = patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': str(root), 'RADIUS_CODEX_BIN': str(executable),
                                         'APPLICATION_AI_MODEL': 'test-model', 'OPENAI_API_KEY': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.runtime.cleanup(); self.session.close(); self.engine.dispose()

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.application_generation'),
                             'Direct application preparation must be implemented')
        return importlib.import_module('hiring_scraper.app.application_generation')

    def request(self, **changes):
        return self.module().PrepareApplicationRequest(job_id=self.job_id, cv_updated_at=self.revision,
            profile_updated_at=self.profile_revision, language='en', **changes)

    def test_snapshot_revision_is_stable_across_dates(self):
        m = self.module()
        with patch.object(m, 'date') as day:
            day.today.return_value.isoformat.return_value = '2026-01-01'
            _, first = m._snapshot(self.profile_id, self.request(), self.session)
            day.today.return_value.isoformat.return_value = '2026-01-02'
            _, second = m._snapshot(self.profile_id, self.request(), self.session)
        self.assertEqual(first, second)

    def documents(self, **changes):
        return {'cv': 'Alex Example\nProduct coordination', 'cover_letter': 'Dear hiring team,\nI coordinate product requirements.',
                'application_answers': 'No additional answers needed.', 'review_notes': 'Emphasized requirements work.',
                'latex_documents': {'cv': latex_source('Alex Example\nProduct coordination'),
                                    'cover_letter': latex_source('Dear hiring team,\nI coordinate product requirements.')},
                'form_answers': None, **changes}

    def upstream(self, documents=None, **changes):
        return {**(documents or self.documents()), **changes}

    def transport(self, result):
        def respond(prompt, schema, **kwargs):
            if schema['title'] == 'ApplicationReview':
                return {'recommendation': 'possible', 'summary': 'Saved coordination experience is relevant.',
                    'requirements': [{'requirement': 'Product requirements', 'priority': 'required',
                        'status': 'supported', 'evidence': 'Saved summary: Product coordination'}],
                    'findings': []}
            return result(prompt, schema) if callable(result) else result
        return respond

    def generate(self, **changes):
        return self.module().prepare_application(self.profile_id, self.request(**changes), self.session)

    def test_invalid_cv_format_is_repaired_once_and_revalidated(self):
        m = self.module()
        invalid = self.documents(latex_documents={'cv': latex_source('Alex').replace('Alex', 'Alex & Co'), 'cover_letter': latex_source('Letter')})
        valid = self.documents()
        calls = []
        def provider(prompt, schema, **kwargs):
            calls.append(prompt)
            return invalid if len(calls) == 1 else valid
        with patch.object(m.codex_runtime, 'generate', side_effect=provider):
            result = self.generate(mode='fast')
        self.assertEqual(len(calls), 2)
        self.assertIn('Escape LaTeX special characters', calls[1])
        self.assertEqual(result['package']['latex_documents']['cv'], valid['latex_documents']['cv'])

    def test_macro_cv_generation_applies_edits_to_original_source(self):
        from test_tex_cv import source
        m = self.module()
        original = source().replace('\\begin{itemize}\n\\item Built research tools.\n\\end{itemize}',
                                    '\\newcommand{\\RadiusWorkOne}{Built research tools.}')
        cv = self.session.get(models.ProfileCV, self.profile_id)
        cv.extracted_text = original
        self.session.commit()
        response = {key: value for key, value in self.documents().items() if key != 'latex_documents'}
        response['cv_edits'] = [{'name': 'RadiusWorkOne', 'text': 'Built useful tools.'}]
        with patch.object(m.codex_runtime, 'generate', return_value=response) as provider:
            result = self.generate(mode='fast')['package']
        self.assertEqual(result['latex_documents']['cv'], original.replace('Built research tools.', 'Built useful tools.'))
        self.assertIn('cv_edits', provider.call_args.args[1]['properties'])

    def test_macro_cv_rejects_unrecognized_edits(self):
        from test_tex_cv import source
        m = self.module()
        original = source().replace('\\begin{itemize}\n\\item Built research tools.\n\\end{itemize}',
                                    '\\newcommand{\\RadiusWorkOne}{Built research tools.}')
        cv = self.session.get(models.ProfileCV, self.profile_id)
        cv.extracted_text = original
        self.session.commit()
        response = {key: value for key, value in self.documents().items() if key != 'latex_documents'}
        response['cv_edits'] = [{'name': 'RadiusUnknown', 'text': 'Changed.'}]
        with patch.object(m.codex_runtime, 'generate', return_value=response), self.assertRaises(HTTPException):
            self.generate(mode='fast')

    def test_macro_cv_corrects_an_overlong_edit(self):
        from test_tex_cv import source
        m = self.module()
        original = source().replace('\\begin{itemize}\n\\item Built research tools.\n\\end{itemize}',
                                    '\\newcommand{\\RadiusWorkOne}{Built research tools.}')
        cv = self.session.get(models.ProfileCV, self.profile_id)
        cv.extracted_text = original
        self.session.commit()
        response = {key: value for key, value in self.documents().items() if key != 'latex_documents'}
        bad = {**response, 'cv_edits': [{'name': 'RadiusWorkOne', 'text': 'Too long ' * 20}]}
        good = {**response, 'cv_edits': [{'name': 'RadiusWorkOne', 'text': 'Built useful tools.'}]}
        with patch.object(m.codex_runtime, 'generate', side_effect=[bad, good]) as provider:
            result = self.generate(mode='fast')['package']
        self.assertEqual(provider.call_count, 2)
        self.assertIn('Built useful tools.', result['latex_documents']['cv'])

    def test_macro_cv_full_review_reconstructs_revised_source(self):
        from test_tex_cv import source
        m = self.module()
        original = source().replace('\\begin{itemize}\n\\item Built research tools.\n\\end{itemize}',
                                    '\\newcommand{\\RadiusWorkOne}{Built research tools.}')
        cv = self.session.get(models.ProfileCV, self.profile_id)
        cv.extracted_text = original
        self.session.commit()
        documents = {key: value for key, value in self.documents().items() if key != 'latex_documents'}
        draft = {**documents, 'cv_edits': [{'name': 'RadiusWorkOne', 'text': 'Built useful tools.'}]}
        revision = {**documents, 'cv_edits': [{'name': 'RadiusWorkOne', 'text': 'Built research apps.'}]}
        review = self.transport(None)('', {'title': 'ApplicationReview'})
        with patch.object(m.codex_runtime, 'generate', side_effect=[draft, review, revision]):
            package = self.generate()['package']
        self.assertEqual(package['latex_documents']['cv'], original.replace('Built research tools.', 'Built research apps.'))

    def test_macro_revision_keeps_unmodified_draft_edits(self):
        from test_tex_cv import source
        m = self.module()
        original = source().replace('\\begin{itemize}\n\\item Built research tools.\n\\end{itemize}',
                                    '\\newcommand{\\RadiusWorkOne}{Built research tools.}\n'
                                    '\\newcommand{\\RadiusWorkTwo}{Designed useful tools.}')
        cv = self.session.get(models.ProfileCV, self.profile_id)
        cv.extracted_text = original
        self.session.commit()
        documents = {key: value for key, value in self.documents().items() if key != 'latex_documents'}
        draft = {**documents, 'cv_edits': [{'name': 'RadiusWorkOne', 'text': 'Built useful tools.'},
                                            {'name': 'RadiusWorkTwo', 'text': 'Designed new tools.'}]}
        revision = {**documents, 'cv_edits': [{'name': 'RadiusWorkTwo', 'text': 'Designed good tools.'}]}
        review = self.transport(None)('', {'title': 'ApplicationReview'})
        with patch.object(m.codex_runtime, 'generate', side_effect=[draft, review, revision]):
            result = self.generate()['package']['latex_documents']['cv']
        self.assertIn('Built useful tools.', result)
        self.assertIn('Designed good tools.', result)

    def test_failed_format_repair_reports_reason_without_publishing(self):
        m = self.module()
        invalid = self.documents(latex_documents={'cv': latex_source('Alex').replace('Alex', 'Alex & Co'), 'cover_letter': latex_source('Letter')})
        with patch.object(m.codex_runtime, 'generate', return_value=invalid) as provider:
            with self.assertRaises(HTTPException) as raised: self.generate(mode='fast')
        self.assertEqual(provider.call_count, 2)
        self.assertIn('formatting', raised.exception.detail.lower())
        self.assertEqual(list(self.session.scalars(select(models.JobApplication))), [])

    def test_latex_in_letter_is_corrected_once_before_publication(self):
        m = self.module()
        bad = self.documents(cover_letter=r'\documentclass{letter}\begin{document}Dear team\end{document}')
        good = self.documents(cover_letter='Dear team, I build research tools.')
        with patch.object(m.codex_runtime, 'generate', side_effect=[bad, good]) as provider:
            result = self.generate(mode='fast')
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(result['package']['cover_letter'], good['cover_letter'])
        self.assertNotIn('documentclass{letter}', result['package']['latex_documents']['cover_letter'].split('\\begin{document}', 1)[-1])

    def test_repeated_latex_in_letter_fails_without_publishing(self):
        m = self.module()
        bad = self.documents(cover_letter=r'Dear \textbf{team}, I build tools.')
        with patch.object(m.codex_runtime, 'generate', return_value=bad) as provider:
            with self.assertRaises(HTTPException) as raised: self.generate(mode='fast')
        self.assertEqual(provider.call_count, 2)
        self.assertIn('cover letter', raised.exception.detail.lower())
        self.assertEqual(list(self.session.scalars(select(models.JobApplication))), [])

    def test_bare_latex_commands_in_letter_are_corrected(self):
        m = self.module()
        bad = self.documents(cover_letter=r'\centering Dear team, I build tools.')
        good = self.documents(cover_letter='Dear team, I build tools.')
        with patch.object(m.codex_runtime, 'generate', side_effect=[bad, good]) as provider:
            result = self.generate(mode='fast')
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(result['package']['cover_letter'], good['cover_letter'])

    def test_math_or_latex_fences_in_letter_trigger_correction(self):
        m = self.module()
        for letter in (r'\(x+y\)', r'\[x^2\]', r'\"o', r'Price is $x^2$ per unit.', '$x + y$', '$E = mc^2$', '$$x+y$$', '```latex\nDear team\n```'):
            with self.subTest(letter=letter), patch.object(m.codex_runtime, 'generate', side_effect=[self.documents(cover_letter=letter), self.documents()]) as provider:
                self.generate(mode='fast')
                self.assertEqual(provider.call_count, 2)
        with patch.object(m.codex_runtime, 'generate', return_value=self.documents(cover_letter='The budget was $5 to $10.')) as provider:
            self.generate(mode='fast')
            self.assertEqual(provider.call_count, 1)

    def test_fast_preparation_uses_selected_model_once(self):
        m = self.module()
        calls = []
        def provider(prompt, schema, **kwargs):
            calls.append((schema['title'], kwargs.get('model')))
            return self.documents()
        with patch.object(m.codex_runtime, 'generate', side_effect=provider):
            result = self.generate(mode='fast', model='example-model')
        self.assertEqual(calls, [('GeneratedDocuments', 'example-model')])
        self.assertIn('independent review', result['package']['review_notes'].lower())

    def test_model_setting_is_exposed_and_validated(self):
        m = self.module()
        with patch.object(m.codex_runtime, 'set_model') as save:
            response = m.set_preparation_model(m.ModelSetting(model='chosen-model'))
        save.assert_called_once_with('chosen-model')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            m.ModelSetting(model='--invalid')

    def test_disabled_configuration_does_not_expose_or_call_provider(self):
        m = self.module()
        with patch.dict(os.environ, {'RADIUS_CODEX_BIN': '/nonexistent/codex'}), patch.object(m.codex_runtime, 'generate') as network:
            self.assertFalse(m.generation_status()['enabled'])
            with self.assertRaises(HTTPException) as raised: self.generate()
            self.assertEqual(raised.exception.status_code, 503)
            network.assert_not_called()
        self.assertNotIn('test-secret', json.dumps(m.generation_status()))

    def test_generates_bound_package_from_saved_sources_without_writing_application_state(self):
        m = self.module()
        def provider(prompt, schema):
            self.assertIn('Alex Example', prompt)
            self.assertIn('Coordinate product requirements.', prompt)
            self.assertIn('Keep my direct voice.', prompt)
            self.assertIn('German', prompt)
            self.assertIn('B2', prompt)
            self.assertIn('Use concise paragraphs.', prompt)
            self.assertFalse(schema['additionalProperties'])
            return self.upstream()
        with patch.object(m.codex_runtime, 'generate', side_effect=self.transport(provider)):
            result = self.generate(notes='Use concise paragraphs.')
        self.assertEqual(result['package']['format'], 'radius-application-v1')
        self.assertEqual(result['package']['job_id'], self.job_id)
        self.assertEqual(result['package']['profile_id'], self.profile_id)
        self.assertEqual(result['package']['cv_updated_at'], self.revision)
        self.assertEqual(result['package']['cover_letter'], 'Dear hiring team,\nI coordinate product requirements.')
        self.assertNotIn('form_answers', result['package'])
        self.assertEqual(bytes(self.session.get(models.ProfileCV, self.profile_id).data), b'original')
        self.assertEqual(list(self.session.scalars(select(models.JobApplication))), [])

    def test_stale_cv_or_profile_is_rejected_before_spending(self):
        m = self.module()
        for field in ['cv_updated_at', 'profile_updated_at']:
            request = self.request().model_copy(update={field: 'outdated'})
            with self.subTest(field=field), patch.object(m.codex_runtime, 'generate') as network:
                with self.assertRaises(HTTPException) as raised:
                    m.prepare_application(self.profile_id, request, self.session)
                self.assertEqual(raised.exception.status_code, 409)
                network.assert_not_called()

    def test_expired_posting_is_rejected_before_spending(self):
        m = self.module()
        job = self.session.get(models.Job, self.job_id)
        job.raw_metadata = {'validThrough': '2000-01-01'}
        self.session.commit()
        with patch.object(m.codex_runtime, 'generate') as network, self.assertRaises(HTTPException) as raised:
            self.generate()
        self.assertEqual(raised.exception.status_code, 409)
        network.assert_not_called()

    def test_capacity_is_bounded_and_released_after_provider_failure(self):
        m = self.module()
        self.assertTrue(m._requests.acquire(blocking=False))
        self.assertTrue(m._requests.acquire(blocking=False))
        try:
            with patch.object(m.codex_runtime, 'generate') as network, self.assertRaises(HTTPException) as raised:
                self.generate()
            self.assertEqual(raised.exception.status_code, 429)
            network.assert_not_called()
        finally:
            m._requests.release(); m._requests.release()
        with patch.object(m.codex_runtime, 'generate', side_effect=HTTPException(504, 'Timed out')), self.assertRaises(HTTPException) as raised:
            self.generate()
        self.assertEqual(raised.exception.status_code, 504)
        with patch.object(m.codex_runtime, 'generate', side_effect=self.transport(self.upstream())):
            self.assertEqual(self.generate()['package']['job_id'], self.job_id)

    def test_context_change_during_generation_rejects_the_result(self):
        m = self.module()
        def provider(*args, **kwargs):
            job = self.session.get(models.Job, self.job_id)
            job.description = 'A materially changed role.'
            self.session.commit()
            return self.upstream()
        with patch.object(m.codex_runtime, 'generate', side_effect=self.transport(provider)), self.assertRaises(HTTPException) as raised:
            self.generate()
        self.assertEqual(raised.exception.status_code, 409)

    def test_invalid_refused_or_incomplete_provider_results_are_not_usable_packages(self):
        m = self.module()
        results = [self.upstream(status='incomplete'),
                   self.upstream(output=[{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'No.'}]}]),
                   {'status': 'completed', 'output': []},
                   self.upstream(self.documents(cv='[TODO write CV]')),
                   self.upstream(self.documents(latex_documents={'cv': latex_source('Safe').replace('Safe', r'\input{/etc/passwd}'),
                                                                'cover_letter': latex_source('Letter')}))]
        for result in results:
            with self.subTest(result=result), patch.object(m.codex_runtime, 'generate', side_effect=self.transport(result)):
                with self.assertRaises(HTTPException) as raised: self.generate()
                self.assertEqual(raised.exception.status_code, 502)

    def test_provider_errors_hide_credentials_and_private_response_bodies(self):
        m = self.module()
        error = HTTPException(503, 'ChatGPT sign-in is required.')
        with patch.object(m.codex_runtime, 'generate', side_effect=error), self.assertRaises(HTTPException) as raised:
            self.generate()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn('test-secret', raised.exception.detail)
        self.assertNotIn('private applicant', raised.exception.detail)

    def test_exported_letter_is_derived_from_the_reviewed_prose(self):
        m = self.module()
        with patch.object(m.codex_runtime, 'generate', side_effect=self.transport(self.upstream(self.documents(
                cover_letter='Dear team,\nI coordinate verified requirements.',
                latex_documents={'cv': latex_source('Alex Example'),
                                 'cover_letter': latex_source('Unreviewed invented achievements.')})))):
            package = self.generate()['package']
        self.assertIn('I coordinate verified requirements.', package['latex_documents']['cover_letter'])
        self.assertNotIn('Unreviewed invented achievements.', package['latex_documents']['cover_letter'])

    def test_saved_marked_history_cannot_be_rewritten_by_model(self):
        from test_tex_cv import source
        m = self.module()
        cv = self.session.get(models.ProfileCV, self.profile_id)
        cv.extracted_text = source(); self.session.commit()
        changed = source().replace('"2021"', '"2010"')
        with patch.object(m.codex_runtime, 'generate', side_effect=self.transport(self.upstream(self.documents(
                latex_documents={'cv': changed, 'cover_letter': latex_source('Letter')})))), self.assertRaises(HTTPException) as raised:
            self.generate()
        self.assertEqual(raised.exception.status_code, 502)

    def test_captured_questions_exclude_manual_and_unknown_answers(self):
        m = self.module()
        form = {'format': 'radius-form-v1', 'job_id': self.job_id, 'profile_id': self.profile_id,
                'form_id': 'a' * 64, 'url': 'https://example.invalid/apply', 'captured_at': int(time.time() * 1000),
                'fields': [{'id': 'q0', 'label': 'Motivation', 'type': 'text', 'required': True,
                            'max_length': 60, 'options': [], 'accept': '', 'document_kind': '', 'manual': False},
                           {'id': 'q1', 'label': 'Consent', 'type': 'checkbox', 'required': True,
                            'max_length': None, 'options': [], 'accept': '', 'document_kind': '', 'manual': True}]}
        for answer in [{'id': 'q1', 'value': 'yes'}, {'id': 'q9', 'value': 'unknown'},
                       {'id': 'q0', 'value': 'x' * 61}]:
            with self.subTest(answer=answer), patch.object(m.codex_runtime, 'generate', side_effect=self.transport(self.upstream(self.documents(
                    form_answers={'form_id': 'a' * 64, 'answers': [answer]})))):
                with self.assertRaises(HTTPException) as raised: self.generate(form=form)
                self.assertEqual(raised.exception.status_code, 502)
        with patch.object(m.codex_runtime, 'generate', side_effect=self.transport(self.upstream(self.documents(
                form_answers={'form_id': 'a' * 64, 'answers': [{'id': 'q0', 'value': 'Requirements work.'}]})))):
            result = self.generate(form=form)
        self.assertEqual(result['package']['form_answers']['answers'], [{'id': 'q0', 'value': 'Requirements work.'}])

    def test_captured_form_url_drops_credentials_query_and_fragment(self):
        m = self.module()
        form = {'format': 'radius-form-v1', 'job_id': self.job_id, 'profile_id': self.profile_id,
                'form_id': 'a' * 64, 'url': 'https://user:password@example.invalid/apply?session=secret#access_token=private',
                'captured_at': int(time.time() * 1000), 'fields': []}
        request = self.request(form=form)
        safe_url = 'https://example.invalid/apply'
        self.assertEqual(request.form.url, safe_url)
        self.assertEqual(request.model_dump()['form']['url'], safe_url)
        data, _ = m._snapshot(self.profile_id, request, self.session)
        self.assertEqual(data['application_form']['url'], safe_url)
        prompt = m._prompt(data)
        for secret in ('user', 'password', 'session=secret', 'access_token=private'):
            self.assertNotIn(secret, prompt)

    def test_original_template_is_preserved_and_locked_header_changes_are_rejected(self):
        from test_tex_cv import source
        m = self.module()
        original = source().replace('\\begin{document}', '\\newcommand{\\MyDesign}{Design}\n\\begin{document}')
        tailored = original.replace('Built research tools.', 'Coordinated research tools.')
        package = m._validate(m.GeneratedDocuments(**self.documents(latex_documents={
            'cv': tailored, 'cover_letter': latex_source('Letter')})), original, None)
        self.assertEqual(package['latex_documents']['cv'], tailored)
        with self.assertRaises(ValueError):
            m._validate(m.GeneratedDocuments(**self.documents(latex_documents={
                'cv': tailored.replace('Alex Example', 'Another Person'),
                'cover_letter': latex_source('Letter')})), original, None)

    def test_canonical_letter_uses_business_letter_template_and_stacked_contacts(self):
        m = self.module()
        prose = 'Alex Example\nalex@example.org\n\nExample GmbH\n\nApplication for Product coordinator\n\nDear Hiring Team,\n\nI coordinate requirements.\n\nKind regards,\nAlex Example'
        package = m._validate(m.GeneratedDocuments(**self.documents(cover_letter=prose)), latex_source('Alex'), None)
        letter = package['latex_documents']['cover_letter']
        self.assertIn(r'\documentclass[11pt,a4paper]{letter}', letter)
        self.assertIn(r'Alex Example\\alex@example.org', letter)

    def test_independent_review_reaches_revision_and_final_checks(self):
        m = self.module()
        stages = []
        draft = self.documents(cover_letter='Dear Hiring Team,\n\nI coordinate work.')
        revised = self.documents(cover_letter='Dear Hiring Team,\n\nI coordinate product requirements.')
        review = {'recommendation': 'possible', 'summary': 'Coordination fits; work permission is unknown.',
            'requirements': [{'requirement': 'Product requirements', 'priority': 'required',
                'status': 'supported', 'evidence': 'Saved summary: Product coordination'},
                {'requirement': 'German work authorization', 'priority': 'required',
                 'status': 'unknown', 'evidence': 'No saved permission status.'}],
            'findings': [{'document': 'cover_letter', 'category': 'coverage',
                          'issue': 'The draft omits requirements work.', 'suggestion': 'Name the verified coordination work.'}]}
        def provider(prompt, schema, **kwargs):
            title = schema['title']; stages.append(title)
            if title == 'ApplicationReview':
                self.assertIn(draft['cover_letter'], json.loads(prompt.split('DRAFT TO REVIEW:\n')[1])['cover_letter'])
                return review
            if len(stages) == 3:
                self.assertIn('The draft omits requirements work.', prompt)
                return revised
            return draft
        with patch.object(m.codex_runtime, 'generate', side_effect=provider):
            result = self.generate()
        self.assertEqual(stages, ['GeneratedDocuments', 'ApplicationReview', 'GeneratedDocuments'])
        self.assertEqual(result['package']['cover_letter'], revised['cover_letter'])
        self.assertIn('German work authorization', result['package']['review_notes'])
        self.assertIn('unknown', result['package']['review_notes'])
        self.assertIn('PDF', result['package']['review_notes'])

    def test_missing_review_or_invalid_revision_never_publishes_the_first_draft(self):
        m = self.module()
        for responses in ([self.documents(), {'summary': 'Incomplete review'}],
                          [self.documents(), self.transport(None)('', {'title': 'ApplicationReview'}),
                           self.documents(cv='[TODO finish revision]')]):
            with self.subTest(responses=responses), patch.object(m.codex_runtime, 'generate', side_effect=responses):
                with self.assertRaises(HTTPException) as raised: self.generate()
                self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(list(self.session.scalars(select(models.JobApplication))), [])

    def test_selected_language_cover_letter_example_reaches_authoritative_context(self):
        m = self.module()
        profile = self.session.get(models.UserProfile, self.profile_id)
        profile.preferences = {**profile.preferences,
            'application_personalization': {'cover_letter_example': 'English example for voice.'},
            'localized_content': {'de': {'application_personalization': {'cover_letter_example': 'Deutsches Beispiel als Stilreferenz.'}}}}
        self.session.commit()
        request = self.request().model_copy(update={'profile_updated_at': profile.updated_at.isoformat(), 'language': 'de'})
        data, _ = m._snapshot(self.profile_id, request, self.session)
        self.assertEqual(data['supporting_profile']['application_personalization']['cover_letter_example'], 'Deutsches Beispiel als Stilreferenz.')
        self.assertEqual(data['writing_preferences']['document_language'], 'de')
        self.assertIsNone(data['supporting_profile']['application_details']['preferences']['authorized_to_work'])

    def test_custom_cv_is_never_compiled_or_claimed_as_pdf_verified(self):
        from test_tex_cv import source
        from hiring_scraper.app import latex_documents
        m = self.module()
        self.assertTrue(callable(getattr(m, '_verify_pdfs', None)), 'Preparation must report which PDFs it could check')
        custom = source().replace('\\begin{document}', '\\newcommand{\\MyDesign}{Design}\n\\begin{document}')
        package = self.documents(latex_documents={'cv': custom, 'cover_letter': latex_source('Letter', letter=True)})
        rendered = []
        original_render = latex_documents.render_latex_pdf
        def render(text, **kwargs):
            self.assertNotEqual(kwargs['source'], custom, 'Uploaded custom commands must never reach LuaTeX')
            rendered.append(kwargs['source'])
            return original_render(text, **kwargs)
        with patch.object(latex_documents, 'render_latex_pdf', side_effect=render):
            checks = m._verify_pdfs(package, {'job': {'description': 'Research required.'}}, [
                {'term': 'research', 'priority': 'required', 'status': 'unknown',
                 'evidence_source': 'none', 'evidence_quote': ''}])
        self.assertEqual(checks[0]['status'], 'not_checked')
        self.assertIn('local', checks[0]['details'])
        self.assertEqual(checks[0]['keywords'][0]['coverage'], 'not_checked')
        self.assertEqual(checks[1]['status'], 'passed')
        self.assertEqual(len(rendered), 1)

    def test_pdf_check_reports_renderer_failure(self):
        from hiring_scraper.app import latex_documents
        m = self.module()
        with patch.object(latex_documents, 'render_latex_pdf', side_effect=RuntimeError('LaTeX engine unavailable')):
            checks = m._verify_pdfs(self.documents(), {})
        self.assertTrue(all('LaTeX engine unavailable' in check['details'] for check in checks))

    def test_pdf_without_extractable_text_is_a_warning(self):
        from io import BytesIO
        from pypdf import PdfWriter
        from hiring_scraper.app import latex_documents
        m = self.module()
        self.assertTrue(callable(getattr(m, '_verify_pdfs', None)), 'Preparation must check PDF extraction')
        writer = PdfWriter(); writer.add_blank_page(width=595, height=842)
        buffer = BytesIO(); writer.write(buffer)
        with patch.object(latex_documents, 'render_latex_pdf', return_value=buffer.getvalue()):
            checks = m._verify_pdfs(self.documents(), {})
        self.assertTrue(all(check['status'] == 'warning' for check in checks))
        self.assertTrue(all('readable text' in check['details'] for check in checks))

    def test_keyword_checks_separate_pdf_presence_from_verified_profile_evidence(self):
        m = self.module()
        self.assertTrue(callable(getattr(m, '_keyword_coverage', None)), 'Report keyword coverage against actual PDF text')
        data = {'job': {'description': '<p>SQL and C++ required; Kubernetes preferred.</p><script>Python</script>'},
            'supporting_profile': {'application_personalization': {
                'career_evidence': 'Used SQL to analyze product feedback.',
                'writing_samples': 'I deployed Kubernetes for a former employer.',
                'career_goals': 'Learn Kubernetes.'}}}
        keywords = [
            {'term': 'SQL', 'priority': 'required', 'status': 'supported', 'evidence_source': 'profile',
             'evidence_quote': 'Used SQL to analyze product feedback.'},
            {'term': 'C++', 'priority': 'required', 'status': 'unknown', 'evidence_source': 'none', 'evidence_quote': ''},
            {'term': 'Kubernetes', 'priority': 'preferred', 'status': 'supported', 'evidence_source': 'profile',
             'evidence_quote': 'I deployed Kubernetes for a former employer.'},
            {'term': 'Python', 'priority': 'required', 'status': 'unknown', 'evidence_source': 'none', 'evidence_quote': ''}]
        rows = m._keyword_coverage(keywords, data, 'Built PostgreSQL tooling in C++.')
        self.assertEqual([row['term'] for row in rows], ['SQL', 'C++', 'Kubernetes'])
        self.assertEqual(rows[0]['coverage'], 'not_found')
        self.assertEqual(rows[0]['evidence'], 'quote_verified')
        self.assertEqual(rows[1]['coverage'], 'literal_match')
        self.assertEqual(rows[1]['assessment'], 'unknown')
        self.assertEqual(rows[2]['evidence'], 'unverified')
        self.assertEqual(rows[2]['assessment'], 'unknown')
        skipped = m._keyword_coverage(keywords, data, None)
        self.assertTrue(all(row['coverage'] == 'not_checked' for row in skipped))
        separate_languages = m._keyword_coverage([
            {'term': term, 'priority': 'required', 'status': 'unknown',
             'evidence_source': 'none', 'evidence_quote': ''} for term in ('C', 'C++')],
            {'job': {'description': 'C and C++ required.'}}, 'C++')
        self.assertEqual(separate_languages[0]['coverage'], 'not_found')
        self.assertEqual(separate_languages[1]['coverage'], 'literal_match')
        from_title = m._keyword_coverage([
            {'term': 'Product Owner', 'priority': 'required', 'status': 'unknown',
             'evidence_source': 'none', 'evidence_quote': ''}],
            {'job': {'title': 'Product Owner', 'description': 'Join our team.'}}, 'Product Owner')
        self.assertEqual(from_title[0]['coverage'] if from_title else None, 'literal_match')

    def test_keyword_coverage_reads_pdf_not_model_prose_or_tex_comments(self):
        from test_tex_cv import source
        m = self.module()
        self.assertTrue(callable(getattr(m, '_keyword_coverage', None)), 'Use the PDF text layer for keyword checks')
        data = {'job': {'description': 'SQL required.'}, 'supporting_profile': {'skills': ['SQL']}}
        keywords = [{'term': 'SQL', 'priority': 'required', 'status': 'supported',
                     'evidence_source': 'profile', 'evidence_quote': 'SQL'}]
        cv_source = source().replace('"Labs"', '"SQL"').replace('Built research tools.', 'Built PostgreSQL tooling.')
        package = self.documents(cv='SQL in model prose only.', latex_documents={
            'cv': cv_source, 'cover_letter': latex_source('Letter', letter=True)})
        checks = m._verify_pdfs(package, data, keywords)
        self.assertEqual(checks[0]['keywords'][0]['coverage'], 'not_found')
        self.assertEqual(checks[0]['keywords'][0]['evidence'], 'quote_verified')

    def test_oversized_synthesized_report_cannot_return_a_browser_invalid_package(self):
        m = self.module()
        for evidence, count in [('E' * 2000, 40), ('😀' * 1500, 36)]:
            review = {'recommendation': 'possible', 'summary': 'Evaluate saved evidence.',
                'requirements': [{'requirement': 'R' * 500, 'priority': 'required',
                    'status': 'unknown', 'evidence': evidence} for _ in range(count)],
                'findings': [], 'keywords': []}
            with self.subTest(unicode=evidence[0]), patch.object(m.codex_runtime, 'generate', side_effect=[self.documents(), review, self.documents()]):
                with self.assertRaises(HTTPException) as raised: self.generate()
                self.assertEqual(raised.exception.status_code, 502)

    def test_unicode_form_answers_obey_browser_and_captured_limits(self):
        m = self.module()
        for limit, value in [(60, '😀' * 31), (None, '😀' * 6000)]:
            form = {'format': 'radius-form-v1', 'job_id': self.job_id, 'profile_id': self.profile_id,
                'form_id': 'a' * 64, 'url': 'https://example.invalid/apply', 'captured_at': int(time.time() * 1000),
                'fields': [{'id': 'q0', 'label': 'Motivation', 'type': 'textarea', 'required': True,
                            'max_length': limit, 'options': [], 'accept': '', 'manual': False}]}
            documents = self.documents(form_answers={'form_id': 'a' * 64, 'answers': [{'id': 'q0', 'value': value}]})
            with self.subTest(limit=limit), patch.object(m.codex_runtime, 'generate', side_effect=self.transport(documents)):
                with self.assertRaises(HTTPException) as raised: self.generate(form=form)
                self.assertEqual(raised.exception.status_code, 502)


    def test_retry_reuses_validated_draft_after_review_timeout(self):
        m = self.module()
        data, _ = m._snapshot(self.profile_id, self.request(), self.session)
        class Control:
            def __init__(self): self.values = {}
            def __call__(self, stage): pass
            def cancelled(self): return False
            def checkpoint(self, key, value): self.values[key] = value
            def load(self, key): return self.values.get(key)
        control = Control()
        review = {'recommendation': 'possible', 'summary': 'Relevant experience.', 'requirements': [{'requirement': 'Coordination', 'priority': 'required', 'status': 'supported', 'evidence': 'Saved experience'}], 'findings': [], 'keywords': []}
        with patch.object(m.codex_runtime, 'generate', side_effect=[self.documents(), HTTPException(504, 'Timed out')]) as provider:
            with self.assertRaises(HTTPException): m._workflow(data, None, control)
            self.assertEqual(provider.call_count, 2)
        with patch.object(m.codex_runtime, 'generate', side_effect=[review, self.documents()]) as provider, patch.object(m, '_verify_pdfs', return_value=[]), patch.object(m, '_review_notes', return_value='Review all facts.'):
            m._workflow(data, None, control)
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(provider.call_args_list[0].args[1]['title'], 'ApplicationReview')

if __name__ == '__main__':
    unittest.main()
