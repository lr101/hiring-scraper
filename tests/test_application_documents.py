"""Application exports render the selected package without changing saved facts."""
import asyncio
from datetime import datetime, timezone
import importlib
from io import BytesIO
import json
import shutil
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from hiring_scraper.app import models, profiles
from test_cv_template import template_docx


class ApplicationDocumentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.profile_id = profiles.create_profile(profiles.ProfileRequest(name='Alex'), self.session)['id']
        company = models.Company(source='test', source_id='export', name='Example')
        feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.invalid/jobs')
        job = models.Job(feed=feed, external_id='1', title='Developer', url='https://example.invalid/job')
        self.session.add(job)
        self.session.commit()
        self.job_id = job.id

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.application_documents'),
                             'Application-document exports must be implemented')
        return importlib.import_module('hiring_scraper.app.application_documents')

    def attach(self, data=None, filename='original.docx'):
        data = template_docx() if data is None else data
        cv = models.ProfileCV(profile_id=self.profile_id, filename=filename,
                              content_type=profiles.TYPES['.docx' if filename.endswith('.docx') else '.txt'],
                              size_bytes=len(data), data=data, extracted_text='Original facts',
                              updated_at=datetime(2026, 10, 18, 12, 34, 56, 123456, timezone.utc))
        self.session.add(cv)
        self.session.commit()
        return profiles.cv_metadata(cv)['updated_at']

    def payload(self, **changes):
        return dict(job_id=self.job_id, cv_updated_at='browser-local-revision', cv='Tailored CV',
                    cover_letter='Dear team,\nI coordinate projects.') | changes

    def export(self, kind='cv', format='docx', profile_id=None, **changes):
        module = self.module()
        return module.application_document(self.profile_id if profile_id is None else profile_id,
                                           module.ApplicationDocumentRequest(**self.payload(**changes)),
                                           kind=kind, format=format, session=self.session)

    def text(self, output):
        with ZipFile(BytesIO(output)) as archive:
            root = ElementTree.fromstring(archive.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        return '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns))

    def test_text_cv_and_letter_docx_contain_selected_unicode_text_and_safe_headers(self):
        for kind, changes, expected in [
            ('cv', {'cv': 'José & <Research>\n\nSecond line 東京'}, 'José & <Research>\n\nSecond line 東京'),
            ('cover_letter', {'cv': '[TODO unused]', 'cover_letter': 'Dear team,\nGrüße & <thanks>'},
             'Dear team,\nGrüße & <thanks>')]:
            with self.subTest(kind=kind):
                response = self.export(kind=kind, **changes)
                self.assertEqual(self.text(response.body), expected)
                self.assertEqual(response.headers['content-type'], profiles.TYPES['.docx'])
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                self.assertIn('.docx"', response.headers['content-disposition'])
        self.assertIsNone(self.session.get(models.ProfileCV, self.profile_id))
        self.assertEqual(self.session.scalars(select(models.JobApplication)).all(), [])

    def test_simple_markdown_exports_as_document_formatting(self):
        markdown = '# Alex Example\n## Experience\n**Research** & <coordination>\n- Led teams\n* Improved delivery'
        response = self.export(cv=markdown)
        self.assertEqual(self.text(response.body), 'Alex Example\nExperience\nResearch & <coordination>\n• Led teams\n• Improved delivery')
        with ZipFile(BytesIO(response.body)) as archive:
            root = ElementTree.fromstring(archive.read('word/document.xml'))
        ns = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paragraphs = root.findall('.//w:body/w:p', ns)
        self.assertIsNotNone(paragraphs[0].find('.//w:b', ns))
        self.assertEqual(paragraphs[0].find('.//w:sz', ns).get('{'+ns['w']+'}val'), '32')
        self.assertIsNotNone(paragraphs[2].find('.//w:b', ns))
        pdf = self.export(cv=markdown, format='pdf')
        from pypdf import PdfReader
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf.body)).pages)
        self.assertIn('Alex Example', text)
        self.assertNotIn('#', text)
        self.assertNotIn('**', text)
        self.assertNotIn('- Led', text)

    def test_paragraph_exports_preserve_unchanged_parts_and_saved_original(self):
        revision = self.attach()
        original = self.session.get(models.ProfileCV, self.profile_id).data
        response = self.export(cv_updated_at=revision, cv=[{'id': 4, 'text': 'Coordinate programs'}])
        self.assertIn('Coordinate programs', self.text(response.body))
        with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(response.body)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(before.read(name), after.read(name), name)
        self.assertEqual(self.session.get(models.ProfileCV, self.profile_id).data, original)
        self.assertEqual(self.session.get(models.ProfileCV, self.profile_id).extracted_text, 'Original facts')

    def test_stale_cv_revision_rejected_for_cv_and_letter_before_rendering(self):
        revision = self.attach()
        for kind in ['cv', 'cover_letter']:
            with self.subTest(kind=kind), self.assertRaises(HTTPException) as raised:
                self.export(kind=kind, cv_updated_at=revision + 'Z')
            self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.text(self.export(cv_updated_at=revision).body), 'Tailored CV')

    def test_unknown_profile_and_job_are_rejected(self):
        for changes in [{'profile_id': 999999}, {'job_id': 999999}]:
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as raised:
                self.export(**changes)
            self.assertEqual(raised.exception.status_code, 404)

    def test_empty_and_unfinished_selected_text_is_rejected(self):
        bad = ['', ' \n ', '[CONFIRM salary]', '[TODO name]', '[TBD]', '[INSERT name]', '[YOUR NAME]',
               'Hello\nTODO: finish', 'TBD — finish', 'please confirm your salary',
               'Can you confirm the following facts', 'Awaiting your approval']
        for kind in ['cv', 'cover_letter']:
            for text in bad:
                with self.subTest(kind=kind, text=text), self.assertRaises(HTTPException) as raised:
                    self.export(kind=kind, **{kind: text})
                self.assertEqual(raised.exception.status_code, 422)

    def test_edits_require_docx_and_valid_unique_paragraph_ids(self):
        for kind in ['cv', 'cover_letter']:
            with self.subTest(kind=kind):
                with self.assertRaises(HTTPException) as raised:
                    self.export(kind=kind, cv=[])
                self.assertEqual(raised.exception.status_code, 422)
        revision = self.attach()
        for edits in [[{'id': 999, 'text': 'Invalid'}], [{'id': 4, 'text': 'A'}] * 2,
                      [{'id': 4, 'text': '[TODO fix]'}]]:
            with self.subTest(edits=edits), self.assertRaises(HTTPException) as raised:
                self.export(cv_updated_at=revision, cv=edits)
            self.assertEqual(raised.exception.status_code, 422)
        self.session.delete(self.session.get(models.ProfileCV, self.profile_id))
        self.session.commit()
        revision = self.attach(b'Plain source', 'cv.txt')
        with self.assertRaises(HTTPException) as raised:
            self.export(cv_updated_at=revision, cv=[])
        self.assertEqual(raised.exception.status_code, 422)

    def test_raw_paragraph_placeholders_are_rejected_before_newline_normalization(self):
        revision = self.attach()
        for text in ['Hello\nTODO: finish', 'Hello\r\nTBD — finish']:
            for format in ['docx', 'pdf']:
                with self.subTest(text=text, format=format):
                    with self.assertRaises(HTTPException) as raised:
                        self.export(format=format, cv_updated_at=revision, cv=[{'id': 4, 'text': text}])
                    self.assertEqual(raised.exception.status_code, 422)
        letter = self.export(kind='cover_letter', cv_updated_at=revision,
                             cv=[{'id': 4, 'text': 'Hello\nTODO: finish'}])
        self.assertEqual(self.text(letter.body), 'Dear team,\nI coordinate projects.')

    def test_template_paragraph_can_be_removed_when_document_remains_nonempty(self):
        revision = self.attach()
        response = self.export(cv_updated_at=revision, cv=[{'id': 4, 'text': ''}])
        self.assertIn('Alex Example', self.text(response.body))
        self.assertNotIn('Coordinate projects', self.text(response.body))

    def test_unedited_template_placeholders_are_rejected(self):
        original = template_docx()
        output = BytesIO()
        with ZipFile(BytesIO(original)) as source, ZipFile(output, 'w') as target:
            for entry in source.infolist():
                data = source.read(entry.filename).replace(b'Coordinate projects', b'[TODO finish]')
                target.writestr(entry, data)
        revision = self.attach(output.getvalue())
        with self.assertRaises(HTTPException) as raised:
            self.export(cv_updated_at=revision, cv=[])
        self.assertEqual(raised.exception.status_code, 422)

    def test_request_fields_are_strict_and_bounded(self):
        module = self.module()
        invalid = [{'job_id': 0}, {'job_id': True}, {'job_id': '1'}, {'cv_updated_at': ''},
                   {'cv_updated_at': ' '}, {'cv_updated_at': 'v' * 201}, {'cv': 123}, {'cv': 'Bad\ud800text'},
                   {'cv': 'x' * 100001}, {'cover_letter': 'x' * 100001}, {'extra': 'ignored?'},
                   {'cv': [{'id': 1, 'text': 'x'}] * 1001},
                   {'cv': [{'id': '1', 'text': 'x'}]}, {'cv': [{'id': True, 'text': 'x'}]},
                   {'cv': [{'id': 1, 'text': 'x' * 5001}]},
                   {'cv': [{'id': i, 'text': 'x' * 5000} for i in range(21)]}]
        from pydantic import ValidationError
        for changes in invalid:
            with self.subTest(fields=list(changes)), self.assertRaises(ValidationError):
                module.ApplicationDocumentRequest(**self.payload(**changes))

    def test_real_pdf_contains_selected_document_and_releases_transaction(self):
        if not (shutil.which('libreoffice') or shutil.which('soffice')):
            self.skipTest('LibreOffice unavailable')
        from pypdf import PdfReader
        self.module()
        from hiring_scraper.app import cv_templates
        real_render = cv_templates.render_pdf
        def render_without_transaction(data):
            self.assertFalse(self.session.in_transaction(), 'Release DB before rendering')
            return real_render(data)
        with patch.object(cv_templates, 'render_pdf', side_effect=render_without_transaction):
            for kind in ['cv', 'cover_letter']:
                response = self.export(kind=kind, format='pdf')
                self.assertEqual(response.headers['content-type'], 'application/pdf')
                text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.body)).pages)
                self.assertIn('Tailored CV' if kind == 'cv' else 'Dear team,', text)
                self.assertNotIn('Original facts', text)

    def test_renderer_errors_and_oversized_outputs_are_actionable(self):
        self.module()
        from hiring_scraper.app import cv_templates
        for failure, status in [(ValueError('Use a safe DOCX.'), 422),
                                (RuntimeError('Download DOCX instead.'), 503)]:
            with patch.object(cv_templates, 'render_pdf', side_effect=failure):
                with self.assertRaises(HTTPException) as raised:
                    self.export(format='pdf')
                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(raised.exception.detail, str(failure))
        with patch.object(cv_templates, 'render_pdf', return_value=b'x' * (5 * 1024 * 1024 + 1)):
            with self.assertRaises(HTTPException) as raised:
                self.export(format='pdf')
            self.assertEqual(raised.exception.status_code, 413)

    def test_invalid_xml_characters_are_rejected_for_both_document_formats(self):
        for text in ['Bad\x00text', 'Bad\x0btext', 'Bad\ufffetext']:
            for format in ['docx', 'pdf']:
                with self.subTest(text=repr(text), format=format):
                    with self.assertRaises(HTTPException) as raised:
                        self.export(cv=text, format=format)
                    self.assertEqual(raised.exception.status_code, 422)

    def test_docx_response_is_bounded_after_template_edits(self):
        output = BytesIO()
        with ZipFile(BytesIO(template_docx())) as source, ZipFile(output, 'w') as target:
            for entry in source.infolist():
                target.writestr(entry, source.read(entry.filename))
            target.writestr('word/media/unchanged.bin', b'x' * (5 * 1024 * 1024 - len(template_docx()) - 2200))
        self.assertLess(len(output.getvalue()), 5 * 1024 * 1024)
        revision = self.attach(output.getvalue())
        with self.assertRaises(HTTPException) as raised:
            self.export(cv_updated_at=revision, cv=[{'id': 4, 'text': 'a' * 5000}])
        self.assertEqual(raised.exception.status_code, 413)

    def test_real_template_pdf_preserves_header_and_contains_requested_edit(self):
        if not (shutil.which('libreoffice') or shutil.which('soffice')):
            self.skipTest('LibreOffice unavailable')
        from pypdf import PdfReader
        revision = self.attach()
        output = self.export(format='pdf', cv_updated_at=revision,
                             cv=[{'id': 4, 'text': 'Coordinate programs'}])
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(output.body)).pages)
        self.assertIn('ORIGINAL CV TEMPLATE', text)
        self.assertIn('Coordinate programs', text)
        self.assertNotIn('Coordinate projects', text)

    def test_router_rejects_unsupported_document_queries_and_defaults_to_pdf(self):
        module = self.module()
        app = FastAPI()
        app.include_router(module.router)
        from hiring_scraper.app.database import get_session
        app.dependency_overrides[get_session] = lambda: self.session
        async def request(query):
            messages = []
            async def receive():
                return {'type': 'http.request', 'body': json.dumps(self.payload()).encode()}
            async def send(message):
                messages.append(message)
            await app({'type': 'http', 'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
                       'path': f'/api/v1/profiles/{self.profile_id}/application-document',
                       'query_string': query, 'headers': [(b'content-type', b'application/json')],
                       'server': ('test', 80), 'client': ('test', 1), 'root_path': ''}, receive, send)
            return messages
        for query in [b'format=html', b'kind=answers', b'format=PDF']:
            messages = asyncio.run(request(query))
            self.assertEqual(messages[0]['status'], 422)
        if shutil.which('libreoffice') or shutil.which('soffice'):
            messages = asyncio.run(request(b''))
            self.assertEqual(messages[0]['status'], 200)
            self.assertIn((b'content-type', b'application/pdf'), messages[0]['headers'])
            self.assertTrue(messages[1]['body'].startswith(b'%PDF-'))
        from hiring_scraper.app import api
        self.assertIn('/api/v1/profiles/{profile_id}/application-document',
                      api.app.openapi()['paths'])
