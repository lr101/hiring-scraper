import importlib
import asyncio
import json
from io import BytesIO
from pathlib import Path
import unittest
from zipfile import ZipFile

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from starlette.requests import Request

from hiring_scraper.app import models, profiles


def sample_pdf(text='Alex Example - Project coordinator'):
    stream = f'BT /F1 12 Tf 50 750 Td ({text}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    data = b'%PDF-1.4\n'
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return data + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()


class ProfileCVTests(unittest.TestCase):
    def test_tex_cv_preserves_source_across_sessions_and_original_download(self):
        # Uploaded source is read as data, including custom templates; it is
        # never executed to extract its content.
        source = '\ufeff\\documentclass{moderncv}\n% My master CV\n\\name{José}{Müller}\n\\section{Experience}\nR\\&D in Genève 2021-2024\n\\input{private-file.tex}\n'
        original = source.encode('utf-8')
        metadata = self.save('Master CV.TEX', original)
        self.assertEqual(metadata['filename'], 'Master CV.TEX')
        self.assertEqual(metadata['content_type'], 'application/x-tex')
        self.assertEqual(metadata['size_bytes'], len(original))
        self.session.close()
        self.session = Session(self.engine)
        stored = self.session.get(models.ProfileCV, self.profile['id'])
        self.assertEqual(stored.extracted_text, source.lstrip('\ufeff').strip())
        payload = json.loads(profiles.cv_text(self.profile['id'], self.session).body)
        self.assertEqual(payload['text'], source.lstrip('\ufeff').strip())
        download = profiles.download_cv(self.profile['id'], self.session)
        self.assertEqual(download.body, original)
        self.assertEqual(download.headers['content-type'], 'application/x-tex')
        self.assertIn('cv.tex', download.headers['content-disposition'])
        self.assertEqual(download.headers['cache-control'], 'no-store')
        self.assertEqual(download.headers['x-content-type-options'], 'nosniff')

    def test_invalid_tex_replacement_keeps_the_previous_cv(self):
        self.save()
        for source in [b'', b'\xff\xfe', b'Alex\x00Example', b' ' * 100, b'x' * 100001,
                       b'x' * (5 * 1024 * 1024 + 1)]:
            with self.subTest(size=len(source)), self.assertRaises(HTTPException) as raised:
                self.save('replacement.tex', source)
            self.assertIn(raised.exception.status_code, [413, 422])
        self.assertEqual(self.session.get(models.ProfileCV, self.profile['id']).filename, 'cv.txt')
        self.assertIn('Verified project coordination', self.session.get(models.ProfileCV, self.profile['id']).extracted_text)

    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.profile = profiles.create_profile(profiles.ProfileRequest(name='Alex'), self.session)
        self.assertTrue(callable(getattr(profiles, 'store_profile_cv', None)), 'Profiles must support a persistent CV attachment')

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def save(self, name='cv.txt', data=b'Alex Example\nVerified project coordination'):
        return profiles.store_profile_cv(self.session, self.profile['id'], name, data)

    def test_cv_survives_new_session_and_is_separate_from_preferences(self):
        metadata = self.save('Alex CV.pdf', sample_pdf())
        self.assertEqual(metadata['filename'], 'Alex CV.pdf')
        self.assertEqual(metadata['content_type'], 'application/pdf')
        self.assertNotIn('text', metadata)
        self.session.close()
        self.session = Session(self.engine)
        stored = self.session.get(models.ProfileCV, self.profile['id'])
        self.assertEqual(stored.data, sample_pdf())
        self.assertIn('Project coordinator', stored.extracted_text)
        profile = profiles.list_profiles(self.session)['items'][0]
        self.assertEqual(profile['cv']['filename'], 'Alex CV.pdf')
        self.assertNotIn('Project coordinator', str(profile))
        profiles.update_profile(self.profile['id'], profiles.ProfileRequest(name='Renamed'), self.session)
        self.assertEqual(self.session.get(models.ProfileCV, self.profile['id']).data, sample_pdf())

    def test_docx_and_utf8_text_can_supply_handoff_text(self):
        data = BytesIO()
        with ZipFile(data, 'w') as zipped:
            zipped.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Alex Example</w:t></w:r></w:p><w:p><w:r><w:t>German B2</w:t></w:r></w:p></w:body></w:document>')
        self.save('cv.docx', data.getvalue())
        self.assertIn('German B2', self.session.get(models.ProfileCV, self.profile['id']).extracted_text)
        self.save('CV.txt', 'José Example\nGerman B2'.encode())
        self.assertEqual(self.session.get(models.ProfileCV, self.profile['id']).extracted_text, 'José Example\nGerman B2')

    def test_invalid_replacement_leaves_the_previous_cv_intact(self):
        self.save()
        for name, data in [('evil.html', b'<script>alert(1)</script>'), ('cv.pdf', b'not a PDF'),
                           ('cv.txt', b''), ('../cv.txt', b'Alex'), ('cv.txt', b'\x00\xff'),
                           ('cv.txt', b'x' * (5 * 1024 * 1024 + 1))]:
            with self.subTest(name=name, size=len(data)), self.assertRaises(HTTPException) as raised:
                self.save(name, data)
            self.assertIn(raised.exception.status_code, [413, 422])
        self.assertEqual(self.session.get(models.ProfileCV, self.profile['id']).filename, 'cv.txt')
        self.assertIn('Verified project', self.session.get(models.ProfileCV, self.profile['id']).extracted_text)

    def test_cv_is_profile_scoped_and_deleted_with_profile(self):
        self.save()
        other = profiles.create_profile(profiles.ProfileRequest(name='Other'), self.session)
        self.assertIsNone(profiles.list_profiles(self.session)['items'][1]['cv'])
        self.assertIsNone(self.session.get(models.ProfileCV, other['id']))
        profiles.delete_profile(self.profile['id'], self.session)
        self.assertIsNone(self.session.get(models.ProfileCV, self.profile['id']))
        with self.assertRaises(HTTPException) as raised:
            profiles.store_profile_cv(self.session, 99999, 'cv.txt', b'Alex')
        self.assertEqual(raised.exception.status_code, 404)

    def test_download_text_removal_and_template_exports(self):
        from test_cv_template import template_docx
        original = template_docx()
        self.save('original.docx', original)
        downloaded = profiles.download_cv(self.profile['id'], self.session)
        self.assertEqual(downloaded.body, original)
        self.assertEqual(downloaded.headers['cache-control'], 'no-store')
        self.assertIn('attachment;', downloaded.headers['content-disposition'])
        text = profiles.cv_text(self.profile['id'], self.session)
        self.assertEqual(text.headers['cache-control'], 'no-store')
        self.assertIn('Alex Example', json.loads(text.body)['text'])
        template = json.loads(profiles.cv_template(self.profile['id'], self.session).body)
        target = next(row for row in template['paragraphs'] if row['text'] == 'Coordinate projects')
        request = profiles.CVExport(source_sha256=template['source_sha256'], changes=[{'id': target['id'], 'text': 'Coordinate programs'}])
        output = profiles.export_cv(self.profile['id'], request, format='docx', preview=False, session=self.session)
        self.assertIn('tailored-cv.docx', output.headers['content-disposition'])
        from hiring_scraper.app.cv_templates import template_paragraphs
        self.assertIn('Coordinate programs', str(template_paragraphs(output.body)))
        self.assertEqual(profiles.download_cv(self.profile['id'], self.session).body, original, 'Export must not replace the saved original')
        self.save('replacement.txt', b'Replacement CV')
        with self.assertRaises(HTTPException) as raised:
            profiles.export_cv(self.profile['id'], request, session=self.session)
        self.assertEqual(raised.exception.status_code, 422)
        self.save('replacement.docx', template_docx() + b' ')
        with self.assertRaises(HTTPException) as raised:
            profiles.export_cv(self.profile['id'], request, session=self.session)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(profiles.delete_cv(self.profile['id'], self.session).status_code, 204)
        self.session.expire_all()
        self.assertIsNone(profiles.list_profiles(self.session)['items'][0]['cv'])

    def test_upload_rejects_oversize_stream_before_storage(self):
        chunks = iter([b'x' * (5 * 1024 * 1024), b'x'])
        async def receive():
            return {'type': 'http.request', 'body': next(chunks), 'more_body': True}
        request = Request({'type': 'http', 'method': 'PUT', 'headers': []}, receive)
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(profiles.upload_cv(self.profile['id'], request, filename='cv.txt', session=self.session))
        self.assertEqual(raised.exception.status_code, 413)
        self.assertIsNone(self.session.get(models.ProfileCV, self.profile['id']))


class CVMigrationTests(unittest.TestCase):
    def test_migration_round_trip_keeps_existing_profiles(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path('hiring_scraper/app/migrations/versions/0028_profile_cv.py')
        self.assertTrue(path.exists(), 'A PostgreSQL-compatible attachment migration is required')
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0028_profile_cv')
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE user_profiles (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('INSERT INTO user_profiles VALUES (1)')
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertIn('profile_cvs', inspect(connection).get_table_names())
                migration.downgrade()
                self.assertNotIn('profile_cvs', inspect(connection).get_table_names())
            self.assertEqual(connection.exec_driver_sql('SELECT id FROM user_profiles').scalar(), 1)
        engine.dispose()
