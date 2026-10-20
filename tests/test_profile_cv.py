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
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.profile = profiles.create_profile(profiles.ProfileRequest(name='Alex'), self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def save(self, name='cv.tex', data=None, language='en'):
        from test_tex_cv import source
        return profiles.store_profile_cv(self.session, self.profile['id'], name,
                                         source().encode() if data is None else data, language=language)

    def test_tex_source_survives_sessions_and_download_without_leaking_into_profile(self):
        from test_tex_cv import source
        original = source().replace('Alex Example', 'José Müller').encode()
        metadata = self.save('Master CV.TEX', original)
        self.assertEqual(metadata['content_type'], 'application/x-tex')
        self.assertEqual(metadata['size_bytes'], len(original))
        self.session.close()
        self.session = Session(self.engine)
        stored = self.session.get(models.ProfileCV, self.profile['id'])
        self.assertEqual(stored.data, original)
        self.assertEqual(stored.extracted_text, original.decode().strip())
        payload = json.loads(profiles.cv_text(self.profile['id'], self.session).body)
        self.assertEqual(payload['text'], original.decode().strip())
        self.assertEqual(payload['experiences'][0]['company'], 'Labs')
        download = profiles.download_cv(self.profile['id'], self.session)
        self.assertEqual(download.body, original)
        self.assertEqual(download.headers['cache-control'], 'no-store')
        self.assertEqual(download.headers['x-content-type-options'], 'nosniff')
        profile = profiles.list_profiles(self.session)['items'][0]
        self.assertNotIn('José Müller', str(profile))
        profiles.update_profile(self.profile['id'], profiles.ProfileRequest(name='Renamed'), self.session)
        self.assertEqual(self.session.get(models.ProfileCV, self.profile['id']).data, original)

    def test_invalid_replacement_keeps_saved_source(self):
        self.save()
        for name, data in [('cv.pdf', sample_pdf()), ('cv.docx', b'zip'), ('cv.txt', b'Alex'),
                           ('../cv.tex', b'Alex'), ('cv.tex', b''), ('cv.tex', b'\x00\xff'),
                           ('cv.tex', b'x' * 100001), ('cv.tex', b'x' * (5 * 1024 * 1024 + 1))]:
            with self.subTest(name=name, size=len(data)), self.assertRaises(HTTPException) as raised:
                self.save(name, data)
            self.assertIn(raised.exception.status_code, [413, 422])
        self.assertEqual(self.session.get(models.ProfileCV, self.profile['id']).filename, 'cv.tex')

    def test_cv_is_profile_scoped_and_removed_with_profile(self):
        from test_tex_cv import source
        self.save()
        other = profiles.create_profile(profiles.ProfileRequest(name='Other'), self.session)
        self.assertIsNone(self.session.get(models.ProfileCV, other['id']))
        profiles.delete_profile(self.profile['id'], self.session)
        self.assertIsNone(self.session.get(models.ProfileCV, self.profile['id']))
        with self.assertRaises(HTTPException) as raised:
            profiles.store_profile_cv(self.session, 99999, 'cv.tex', source().encode())
        self.assertEqual(raised.exception.status_code, 404)

    def test_removing_cvs_removes_both_languages(self):
        self.save(language='en')
        self.save(language='de')
        self.assertEqual(profiles.delete_cv(self.profile['id'], self.session).status_code, 204)
        self.session.expire_all()
        self.assertIsNone(profiles.list_profiles(self.session)['items'][0]['cv'])

    def test_upload_rejects_oversize_stream_before_storage(self):
        chunks = iter([b'x' * (5 * 1024 * 1024), b'x'])
        async def receive():
            return {'type': 'http.request', 'body': next(chunks), 'more_body': True}
        request = Request({'type': 'http', 'method': 'PUT', 'headers': []}, receive)
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(profiles.upload_cv(self.profile['id'], request, filename='cv.tex', session=self.session))
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
