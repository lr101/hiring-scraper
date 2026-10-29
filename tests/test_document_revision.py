"""Prompted changes must be grounded, validated and kept as proposals."""
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from hiring_scraper.app import document_revision as revision
from hiring_scraper.app.latex_documents import latex_source
from test_tex_cv import source

class DocumentRevisionTests(unittest.TestCase):
    def test_letter_revision_uses_saved_facts_and_returns_only_a_proposal(self):
        data = {'original_cv_text': source(), 'job': {'title':'Engineer'}, 'supporting_profile':{}, 'writing_preferences': {'document_language':'en'}}
        request = revision.DocumentRevisionRequest(job_id=1, cv_updated_at='c', profile_updated_at='p', language='en', target='cover_letter', instruction='Make the opening more direct', current_document='Dear team, I build tools.')
        with patch.object(revision, '_generate', return_value=revision.RevisionOutput(document='Dear team, I build research tools.', review='The opening can be clearer.', changes='Made the opening more direct.')) as generate:
            result = revision.revise_document(data, request)
        self.assertEqual(result['document'], 'Dear team, I build research tools.')
        self.assertEqual(result['target'], 'cover_letter')
        prompt = generate.call_args.args[0]
        self.assertIn('Make the opening more direct', prompt)
        self.assertIn('original_cv_text', prompt)
        self.assertIn('untrusted', prompt.lower())

    def test_letter_revision_corrects_latex_before_returning(self):
        data = {'original_cv_text': source(), 'job': {}, 'supporting_profile': {}, 'writing_preferences': {}}
        request = revision.DocumentRevisionRequest(job_id=1, cv_updated_at='c', profile_updated_at='p', language='en', target='cover_letter', instruction='Be direct', current_document='Dear team.')
        bad = revision.RevisionOutput(document=r'Dear \textbf{team}', review='Review.', changes='Changed.')
        good = revision.RevisionOutput(document='Dear team, I build tools.', review='Review.', changes='Changed.')
        with patch.object(revision, '_generate', side_effect=[bad, good]) as generate:
            result = revision.revise_document(data, request)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(result['document'], good.document)

    def test_letter_revision_rejects_repeated_latex(self):
        data = {'original_cv_text': source(), 'job': {}, 'supporting_profile': {}, 'writing_preferences': {}}
        request = revision.DocumentRevisionRequest(job_id=1, cv_updated_at='c', profile_updated_at='p', language='en', target='cover_letter', instruction='Be direct', current_document='Dear team.')
        bad = revision.RevisionOutput(document=r'Dear \textbf{team}', review='Review.', changes='Changed.')
        with patch.object(revision, '_generate', return_value=bad) as generate:
            with self.assertRaises(HTTPException) as error: revision.revise_document(data, request)
        self.assertEqual(generate.call_count, 2)
        self.assertIn('cover letter', error.exception.detail.lower())

    def test_cv_revision_rejects_changed_history_and_unfinished_output(self):
        original = source()
        request = revision.DocumentRevisionRequest(job_id=1, cv_updated_at='c', profile_updated_at='p', language='en', target='cv', instruction='Be concise', current_document=original)
        data = {'original_cv_text': original, 'job': {}, 'supporting_profile':{}, 'writing_preferences': {}}
        for bad in (original.replace('"2021"', '"2001"'), original.replace('Built research tools.', '[TODO write CV]')):
            with self.subTest(), patch.object(revision, '_generate', return_value=revision.RevisionOutput(document=bad, review='Review.', changes='Changed.')):
                with self.assertRaises((HTTPException, ValueError)): revision.revise_document(data, request)

    def test_check_rejects_suggestion_after_source_change(self):
        from fastapi import Response
        request = revision.DocumentRevisionRequest(job_id=1, cv_updated_at='c', profile_updated_at='p', language='en', target='cover_letter', instruction='Be direct', current_document='Dear team.')
        record = {'id':'suggestion', 'state':'completed', 'revision':'original', 'request':request.model_dump(), 'result':{'target':'cover_letter','document':'Dear team, revised.'}}
        with patch.object(revision, 'require_profile'), patch.object(revision.application_generation_jobs, 'status', return_value=record), patch.object(revision, '_snapshot', return_value=({}, 'changed')):
            with self.assertRaises(HTTPException) as error:
                revision.check_revision(7, 1, 'suggestion', Response(), object())
        self.assertEqual(error.exception.status_code, 409)

    def test_discard_does_not_delete_a_newer_revision(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            revision.application_generation_jobs._write(root, 7, 1, {'id':'new', 'state':'completed', 'updated_at':__import__('time').time()})
            with self.assertRaises(HTTPException):
                revision.application_generation_jobs.delete_if_id(root, 7, 1, 'old')
            self.assertEqual(revision.application_generation_jobs.status(root, 7, 1)['id'], 'new')

    def test_saving_a_draft_rejects_changed_profile(self):
        from fastapi import Response
        from hiring_scraper.app import application_generation as generation
        from types import SimpleNamespace
        profile = SimpleNamespace(updated_at=__import__('datetime').datetime.fromisoformat('2026-10-27T10:00:00'))
        request = generation.SavedDraftRequest(version=None, draft={'context': {'job_id':1, 'profile_id':7, 'cv_updated_at':'c'}, 'profile_updated_at':'2026-10-26T10:00:00'})
        with patch.object(generation, 'require_profile', return_value=profile), patch.object(generation, 'require_cv'), patch.object(generation, 'cv_metadata', return_value={'updated_at':'c'}), patch.object(generation.application_generation_jobs, 'save_draft') as save:
            with self.assertRaises(HTTPException) as error: generation.save_application_draft(7, 1, request, Response(), SimpleNamespace(get=lambda *args: object()))
        self.assertEqual(error.exception.status_code, 409)
        save.assert_not_called()
