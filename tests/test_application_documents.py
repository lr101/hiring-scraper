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
    def test_saved_profile_photo_is_used_by_default_and_can_be_overridden_or_excluded(self):
        import base64
        original = base64.b64decode(self.photo().split(',', 1)[1])
        self.assertTrue(callable(getattr(profiles, 'store_profile_photo', None)), 'Saved profile portraits are missing')
        profiles.store_profile_photo(self.session, self.profile_id, 'portrait.png', original)
        response = self.export(format='tex_bundle')
        with ZipFile(BytesIO(response.body)) as bundle:
            self.assertEqual(bundle.read('portrait.png'), original)
            self.assertIn('includegraphics', bundle.read('application.tex').decode())
        # An application override changes this export, never the saved portrait.
        from PIL import Image
        output = BytesIO()
        Image.new('RGB', (4, 4), 'red').save(output, format='PNG')
        override = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        with ZipFile(BytesIO(self.export(format='tex_bundle', photo_data_url=override).body)) as bundle:
            self.assertEqual(bundle.read('portrait.png'), output.getvalue())
        self.assertEqual(profiles.download_photo(self.profile_id, self.session).body, original)
        with self.assertRaises(HTTPException) as error:
            self.export(format='tex_bundle', use_profile_photo=False)
        self.assertEqual(error.exception.status_code, 422)
        # Letters and raw TeX exports must keep working with a saved photo.
        self.assertNotIn('includegraphics', self.export(kind='cover_letter', format='tex').body.decode())
        self.assertNotIn('includegraphics', self.export(format='tex').body.decode())

    @unittest.skipUnless(shutil.which('lualatex'), 'LuaLaTeX unavailable')
    def test_saved_photo_is_embedded_in_pdf_unless_excluded(self):
        import base64
        from pypdf import PdfReader
        self.assertTrue(callable(getattr(profiles, 'store_profile_photo', None)), 'Saved profile portraits are missing')
        profiles.store_profile_photo(self.session, self.profile_id, 'portrait.png', base64.b64decode(self.photo().split(',', 1)[1]))
        def image_count(response):
            return sum(len(page.images) for page in PdfReader(BytesIO(response.body)).pages)
        self.assertGreater(image_count(self.export(format='latex_pdf')), 0)
        self.assertEqual(image_count(self.export(format='latex_pdf', use_profile_photo=False)), 0)
        self.assertEqual(image_count(self.export(kind='cover_letter', format='latex_pdf')), 0)

    def test_cover_letter_export_uses_readable_text_not_supplied_latex(self):
        from hiring_scraper.app.latex_documents import latex_source
        response = self.export(kind='cover_letter', format='tex', cover_letter='Dear team, my current letter.',
                               latex_documents={'cover_letter': latex_source('Older different letter.')})
        self.assertIn('my current letter', response.body.decode())
        self.assertNotIn('Older different letter', response.body.decode())

    def test_canvas_latex_source_is_exported_verbatim_and_used_for_pdf(self):
        from hiring_scraper.app.latex_documents import latex_source
        source = latex_source('# Canvas-only heading\nJosé in Genève 2021-2024')
        letter_source = latex_source('Canvas-only letter')
        sources = {'cv': source, 'cover_letter': letter_source}
        response = self.export(format='tex', latex_documents=sources)
        self.assertEqual(response.body.decode(), source)
        response = self.export(kind='cover_letter', format='tex', latex_documents=sources)
        self.assertIn('Dear', response.body.decode())
        self.assertNotIn('Canvas-only letter', response.body.decode())
        if shutil.which('lualatex'):
            from pypdf import PdfReader
            response = self.export(format='latex_pdf', latex_documents=sources)
            text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(response.body)).pages)
            self.assertIn('Canvas-only heading', text)
            self.assertIn('José in Genève 2021-2024', text)
            self.assertNotIn('Tailored CV', text)

    def test_business_letter_line_breaks_and_no_page_number_render_safely(self):
        from hiring_scraper.app.latex_documents import latex_source, validate_latex_source
        source = latex_source('Letter body.').replace('Letter body.' + r'\par',
            r'\thispagestyle{empty}' + '\n' + r'Alex Example\\Bonn\par' + '\n' +
            r'Labs\\Hiring Team\par' + '\n' + r'Application for Engineer\par' + '\n' +
            r'Dear Hiring Team,\par' + '\n' + r'Letter body.\par' + '\n' + r'Sincerely,\\Alex Example\par')
        self.assertEqual(validate_latex_source(source), source)
        if shutil.which('lualatex'):
            from pypdf import PdfReader
            response = self.export(kind='cover_letter', format='latex_pdf', cover_letter='Alex Example\nBonn\n\nLabs\nHiring Team\n\nApplication for Engineer\n\nDear Hiring Team,\n\nLetter body.\n\nSincerely,\nAlex Example', latex_documents={'cover_letter': source})
            pdf = PdfReader(BytesIO(response.body))
            self.assertEqual(len(pdf.pages), 1)
            text = pdf.pages[0].extract_text()
            self.assertIn('Application for Engineer', text)
            self.assertFalse(text.rstrip().endswith('1'))
        with self.assertRaises(ValueError):
            validate_latex_source(source.replace(r'\thispagestyle{empty}', r'\thispagestyle{\input{secret}}'))

    def test_canvas_latex_rejects_executable_commands_and_preamble_changes(self):
        from hiring_scraper.app.latex_documents import latex_source
        from pydantic import ValidationError
        for text in [r'\input{/etc/passwd}', r'\directlua{os.execute("id")}', r'\catcode`x=0',
                     r'\csname input\endcsname', r'^^5cinput{/etc/passwd}', r'\begin{filecontents}{pwn}x\end{filecontents}',
                     '% hidden comment', r'\section*{unclosed', r'\end{itemize}', '[TODO write CV]']:
            source = latex_source('safe').replace('safe\\par', text)
            with self.subTest(text=text), self.assertRaises((HTTPException, ValidationError)):
                self.export(format='latex_pdf', latex_documents={'cv': source})
        source = latex_source('safe').replace('margin=22mm', 'margin=1mm')
        with self.assertRaises((HTTPException, ValidationError)):
            self.export(format='tex', latex_documents={'cv': source})

    def test_legacy_letter_sources_do_not_override_the_readable_letter(self):
        from hiring_scraper.app.latex_documents import _TEMPLATE
        for body in [r'\section*{Old letter}', r'\input{secret}', r'\begin{itemize}\item Old letter.\end{itemize}']:
            source = _TEMPLATE['preamble'] + body + '\n' + _TEMPLATE['ending']
            response = self.export(kind='cover_letter', format='tex', cover_letter='Dear team, current letter.', latex_documents={'cover_letter': source})
            self.assertIn('current letter', response.body.decode())
            self.assertNotIn(body, response.body.decode())

    def test_latex_source_escapes_prose_and_keeps_selected_document(self):
        response = self.export(format='tex', cv='# José Müller\n## Experience\n- R&D: 20% with C_1 and $5\n\\input{/etc/passwd} ~ ^ { }',
                               cover_letter='Unused letter')
        source = response.body.decode('utf-8')
        self.assertIn('José Müller', source)
        self.assertIn(r'\section*{Experience}', source)
        self.assertIn(r'R\&D: 20\% with C\_1 and \$5', source)
        self.assertIn(r'\textbackslash{}input\{/etc/passwd\}', source)
        self.assertNotIn(r'\input{', source)
        self.assertNotIn('Unused letter', source)
        self.assertIn('.tex"', response.headers['content-disposition'])
        self.assertIn('text/plain', response.headers['content-type'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        letter = self.export(kind='cover_letter', format='tex', cover_letter='Dear team,\nGrüße & thanks')
        self.assertIn(r'Grüße \& thanks', letter.body.decode())
        self.assertNotIn('Tailored CV', letter.body.decode())

    def test_latex_from_template_uses_entire_cv_after_edits_without_changing_original(self):
        revision = self.attach()
        original = bytes(self.session.get(models.ProfileCV, self.profile_id).data)
        output = self.export(format='tex', cv_updated_at=revision, cv=[{'id': 4, 'text': 'Coordinate programs'}])
        source = output.body.decode()
        self.assertIn('ORIGINAL CV TEMPLATE', source)
        self.assertIn('Coordinate programs', source)
        self.assertNotIn('Coordinate projects', source)
        self.assertEqual(bytes(self.session.get(models.ProfileCV, self.profile_id).data), original)

    def test_latex_rejects_unfinished_content_and_stale_cv(self):
        with self.assertRaises(HTTPException) as raised:
            self.export(format='tex', cv='[TODO add achievements]')
        self.assertEqual(raised.exception.status_code, 422)
        self.attach()
        with self.assertRaises(HTTPException) as raised:
            self.export(format='tex')
        self.assertEqual(raised.exception.status_code, 409)

    def test_latex_pdf_without_engine_offers_source_and_source_still_works(self):
        with patch('hiring_scraper.app.latex_documents.shutil.which', return_value=None):
            with self.assertRaises(HTTPException) as raised:
                self.export(format='latex_pdf')
            self.assertEqual(raised.exception.status_code, 503)
            self.assertIn('source', raised.exception.detail)
            self.assertIn(r'\begin{document}', self.export(format='tex').body.decode())

    def test_latex_reports_unsupported_glyphs_instead_of_silently_losing_text(self):
        if not shutil.which('lualatex'):
            self.skipTest('LuaLaTeX unavailable')
        with self.assertRaises(HTTPException) as raised:
            self.export(format='latex_pdf', cv='Alex Example 東京')
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn('font', raised.exception.detail)

    def test_real_latex_pdf_has_unicode_text_dates_and_no_executed_tex(self):
        if not shutil.which('lualatex'):
            self.skipTest('LuaLaTeX unavailable')
        from pypdf import PdfReader
        for kind in ['cv', 'cover_letter']:
            with self.subTest(kind=kind):
                text = '# José Müller\n## Experience\n- R&D in Genève 2021-2024: 20% faster\n\\input{/etc/passwd}'
                if kind == 'cover_letter':
                    with self.assertRaises(HTTPException): self.export(kind=kind, format='latex_pdf', cv=text, cover_letter=text)
                    text = text.replace('\\input{/etc/passwd}', 'Safe closing.')
                response = self.export(kind=kind, format='latex_pdf', cv=text, cover_letter=text)
                reader = PdfReader(BytesIO(response.body))
                extracted = '\n'.join(page.extract_text() for page in reader.pages)
                for expected in ['José Müller', 'R&D', 'Genève', '2021-2024', '20%', 'input{/etc/passwd}' if kind == 'cv' else 'Safe closing.']:
                    self.assertIn(expected, extracted)
                self.assertNotIn('root:x:', extracted)
                self.assertEqual(len(reader.pages), 1)
                self.assertEqual(response.headers['content-type'], 'application/pdf')
                self.assertIn('.pdf"', response.headers['content-disposition'])

    def test_letter_export_keeps_compact_sender_and_recipient_blocks(self):
        letter = 'Alex Example\nBonn, Germany\n+49 123 456\nalex@example.org\n\nExample Ltd\nHiring Team\n\n25 October 2026\n\n**Application for Engineer**\n\nDear Hiring Team,\n\nI build useful tools.\n\nYours sincerely,\nAlex Example'
        source = self.export(kind='cover_letter', format='tex', cover_letter=letter).body.decode()
        self.assertIn(r'Alex Example\\Bonn, Germany\\+49 123 456\\alex@example.org\par', source)
        self.assertIn(r'Example Ltd\\Hiring Team\par', source)
        self.assertIn(r'\thispagestyle{empty}', source)
        self.assertIn(r'\documentclass[11pt,a4paper]{letter}', source)
        self.assertIn(r'\textbf{Application for Engineer}', source)

    @unittest.skipUnless(shutil.which('lualatex'), 'LuaLaTeX unavailable')
    def test_letter_accepts_extra_blank_lines_between_address_blocks(self):
        from pypdf import PdfReader
        response = self.export(kind='cover_letter', format='latex_pdf', cover_letter='Alex Example\nBonn\n\n\nExample Ltd\n\n\nDear team,\n\nI build tools.')
        self.assertIn('Example Ltd', PdfReader(BytesIO(response.body)).pages[0].extract_text())

    def test_preserved_custom_cv_pdf_can_be_disabled_and_uses_custom_renderer(self):
        from test_tex_cv import source
        original = source().replace(r'\begin{document}', r'\usepackage{graphicx}' + '\n' + r'\begin{document}')
        revision = profiles.store_profile_cv(self.session, self.profile_id, 'original.tex', original.encode())['updated_at']
        with patch.dict('os.environ', {'RADIUS_TRUST_CUSTOM_LATEX': '0'}):
            with self.assertRaises(HTTPException):
                self.export(format='latex_pdf', cv_updated_at=revision, latex_documents={'cv': original})
        with patch.dict('os.environ', {}, clear=True), patch('hiring_scraper.app.latex_documents.render_custom_latex_pdf', return_value=b'%PDF-test') as render:
            response = self.export(format='latex_pdf', cv_updated_at=revision, latex_documents={'cv': original})
        self.assertEqual(response.body, b'%PDF-test')
        self.assertEqual(render.call_args.kwargs['source'], original)

    @unittest.skipUnless(shutil.which('xelatex'), 'XeLaTeX unavailable')
    def test_custom_cv_pdf_renders_saved_layout_and_portrait(self):
        from test_tex_cv import source
        from pypdf import PdfReader
        original = source().replace(r'\begin{document}', r'\usepackage{graphicx}' + '\n' + r'\begin{document}' + '\n' + r'\includegraphics[width=1cm]{portrait_cropped.png}')
        revision = profiles.store_profile_cv(self.session, self.profile_id, 'original.tex', original.encode())['updated_at']
        response = self.export(format='latex_pdf', cv_updated_at=revision, latex_documents={'cv': original}, photo_data_url=self.photo())
        pdf = PdfReader(BytesIO(response.body))
        self.assertTrue(any(page.extract_text().strip() for page in pdf.pages))
        self.assertEqual(sum(len(page.images) for page in pdf.pages), 1)

    def test_custom_cv_rejects_tex_character_escape_in_tailored_prose(self):
        from test_tex_cv import source
        original = source().replace(r'\begin{document}', r'\usepackage{graphicx}' + '\n' + r'\begin{document}')
        revision = profiles.store_profile_cv(self.session, self.profile_id, 'original.tex', original.encode())['updated_at']
        tailored = original.replace('Built', '^^5cinput')
        self.assertNotEqual(tailored, original)
        with patch('hiring_scraper.app.latex_documents.render_custom_latex_pdf', side_effect=AssertionError('Must not execute')):
            with self.assertRaises(HTTPException):
                self.export(format='latex_pdf', cv_updated_at=revision, latex_documents={'cv': tailored})

    def test_custom_cv_photo_bundle_preserves_source_header_and_asset(self):
        from test_tex_cv import source
        original = source().replace(r'\begin{document}', r'\begin{document}' + '\n' + r'\includegraphics[width=25mm]{portrait_cropped.png}' + '\nAlex Example | alex@example.org\n')
        revision = profiles.store_profile_cv(self.session, self.profile_id, 'original.tex', original.encode())['updated_at']
        # Attaching arbitrary templates never enables their server compilation.
        photo = self.photo()
        with patch('hiring_scraper.app.latex_documents.subprocess.Popen', side_effect=AssertionError('Custom TeX must never run')):
            response = self.export(format='tex_bundle', cv_updated_at=revision,
                                   latex_documents={'cv': original}, photo_data_url=photo)
        self.assertEqual(response.headers['content-type'], 'application/zip')
        with ZipFile(BytesIO(response.body)) as bundle:
            self.assertEqual(set(bundle.namelist()), {'application.tex', 'portrait_cropped.png', 'README.txt'})
            self.assertEqual(bundle.read('application.tex').decode(), original)
            self.assertIn(b'lualatex -no-shell-escape', bundle.read('README.txt'))
            self.assertTrue(bundle.read('portrait_cropped.png').startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(bytes(self.session.get(models.ProfileCV, self.profile_id).data), original.encode())

    def test_browser_local_cv_photo_bundle_never_compiles_custom_source(self):
        from test_tex_cv import source
        original = source().replace(r'\begin{document}', r'\begin{document}' + '\n' + r'\includegraphics{portrait.png}')
        with patch('hiring_scraper.app.latex_documents.subprocess.Popen', side_effect=AssertionError('Custom TeX must never run')):
            response = self.export(format='tex_bundle', latex_documents={'cv': original}, photo_data_url=self.photo())
        with ZipFile(BytesIO(response.body)) as bundle:
            self.assertEqual(bundle.read('application.tex').decode(), original)

    def test_photo_is_bounded_and_never_injected_into_letter_or_custom_tex(self):
        from hiring_scraper.app.latex_documents import latex_source
        for photo in ['data:image/png;base64,not-base64', 'data:image/png;base64,SGVsbG8=', self.photo(width=10000)]:
            with self.subTest(photo=photo[:40]), self.assertRaises(HTTPException):
                self.export(format='latex_pdf', photo_data_url=photo)
        with self.assertRaises(HTTPException):
            self.export(kind='cover_letter', format='tex_bundle', photo_data_url=self.photo())
        # Raw source download stays exact; an image requires the source bundle.
        with self.assertRaises(HTTPException):
            self.export(format='tex', latex_documents={'cv': latex_source('Alex')}, photo_data_url=self.photo())

    def test_truncated_png_is_rejected_before_engine_or_bundle(self):
        import base64
        data = base64.b64decode(self.photo().split(',', 1)[1])
        for broken in [data[:33], data[:-12], data[:-1], data[:40] + b'corrupt' + data[47:]]:
            for format in ['latex_pdf', 'tex_bundle']:
                with self.subTest(format=format, length=len(broken)), patch('hiring_scraper.app.latex_documents.subprocess.Popen', side_effect=AssertionError('Invalid PNG must never reach the engine')):
                    with self.assertRaises(HTTPException) as raised:
                        self.export(format=format, photo_data_url='data:image/png;base64,' + base64.b64encode(broken).decode())
                    self.assertEqual(raised.exception.status_code, 422)

    def test_crc_correct_png_with_invalid_pixels_is_rejected_before_engine(self):
        import base64
        import struct
        import zlib
        def chunk(kind, data):
            return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
        for depth, pixels in [(3, b'x'), (8, b'x')]:
            data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 24, 36, depth, 0, 0, 0, 0)) + chunk(b'IDAT', pixels) + chunk(b'IEND', b'')
            with self.subTest(depth=depth), patch('hiring_scraper.app.latex_documents.subprocess.Popen', side_effect=AssertionError('Invalid pixels must never reach the engine')):
                with self.assertRaises(HTTPException) as raised:
                    self.export(format='latex_pdf', photo_data_url='data:image/png;base64,' + base64.b64encode(data).decode())
                self.assertEqual(raised.exception.status_code, 422)

    @unittest.skipUnless(shutil.which('lualatex'), 'LuaLaTeX unavailable')
    def test_photo_pdf_keeps_header_text_and_embeds_image(self):
        from pypdf import PdfReader
        response = self.export(format='latex_pdf', cv='# Alex Example\nalex@example.org\n\n## Experience\nBuilt useful tools.', photo_data_url=self.photo())
        pdf = PdfReader(BytesIO(response.body))
        text = '\n'.join(page.extract_text() for page in pdf.pages)
        self.assertIn('Alex Example', text)
        self.assertIn('alex@example.org', text)
        self.assertIn('Built useful tools.', text)
        self.assertEqual(len(pdf.pages), 1)
        self.assertTrue(any(obj.get_object().get('/Subtype') == '/Image' for page in pdf.pages for obj in page['/Resources'].get('/XObject', {}).get_object().values()))

    @staticmethod
    def photo(width=24):
        import base64
        import struct
        import zlib
        def chunk(kind, data):
            return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
        data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', width, 36, 8, 2, 0, 0, 0))
        data += chunk(b'IDAT', zlib.compress((b'\0' + b'\x35\x67\x89' * 24) * 36)) + chunk(b'IEND', b'')
        return 'data:image/png;base64,' + base64.b64encode(data).decode()

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
