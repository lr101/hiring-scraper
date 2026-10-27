"""Saved portraits survive edits and are isolated from profile prose and other profiles."""
import asyncio
import base64
import importlib
import json
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from hiring_scraper.app import models, profiles
import test_application_documents as document_tests


class ProfilePhotoTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.profile_id = profiles.create_profile(profiles.ProfileRequest(name='Alex'), self.session)['id']
        self.png = base64.b64decode(document_tests.ApplicationDocumentTests.photo().split(',', 1)[1])

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def upload(self, data=None, filename='portrait.png', profile_id=None):
        self.assertTrue(callable(getattr(profiles, 'upload_photo', None)), 'Profile photo upload is missing')
        async def receive():
            return {'type': 'http.request', 'body': self.png if data is None else data}
        request = Request({'type': 'http', 'method': 'PUT', 'headers': []}, receive)
        return asyncio.run(profiles.upload_photo(profile_id or self.profile_id, request,
                                                filename=filename, session=self.session))

    def test_photo_survives_reload_and_profile_edits_without_leaking_pixels_into_json(self):
        metadata = self.upload()
        self.assertEqual(metadata['filename'], 'portrait.png')
        self.assertEqual(metadata['content_type'], 'image/png')
        self.assertEqual(metadata['size_bytes'], len(self.png))
        self.session.close()
        self.session = Session(self.engine)
        profile = profiles.list_profiles(self.session)['items'][0]
        self.assertEqual(profile['photo'], metadata)
        self.assertNotIn(base64.b64encode(self.png).decode(), json.dumps(profile))
        self.assertNotIn('photo', self.session.get(models.UserProfile, self.profile_id).preferences)
        profiles.update_profile(self.profile_id, profiles.ProfileRequest(name='Renamed'), self.session)
        response = profiles.download_photo(self.profile_id, self.session)
        self.assertEqual(response.body, self.png)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')

    def test_invalid_replacement_keeps_saved_photo(self):
        self.upload()
        for data, filename in [(b'bad', 'portrait.png'), (b'', 'portrait.png'),
                               (self.png[:-1], 'portrait.png'), (self.png, '../portrait.png'),
                               (self.png, 'portrait.svg'), (b'x' * (2 * 1024 * 1024 + 1), 'portrait.png'),
                               (base64.b64decode(document_tests.ApplicationDocumentTests.photo(width=10000).split(',', 1)[1]), 'portrait.png')]:
            with self.subTest(filename=filename, size=len(data)), self.assertRaises(HTTPException) as error:
                self.upload(data, filename)
            self.assertIn(error.exception.status_code, [413, 422])
        self.assertEqual(profiles.download_photo(self.profile_id, self.session).body, self.png)

    def test_replace_remove_and_profile_deletion_are_profile_scoped(self):
        self.upload()
        other = profiles.create_profile(profiles.ProfileRequest(name='Other'), self.session)
        with self.assertRaises(HTTPException) as error:
            profiles.download_photo(other['id'], self.session)
        self.assertEqual(error.exception.status_code, 404)
        from io import BytesIO
        from PIL import Image
        output = BytesIO()
        Image.new('RGB', (8, 8), 'blue').save(output, format='PNG')
        replacement = output.getvalue()
        self.upload(replacement, 'new.png')
        self.assertEqual(profiles.list_profiles(self.session)['items'][0]['photo']['filename'], 'new.png')
        self.assertEqual(profiles.download_photo(self.profile_id, self.session).body, replacement)
        self.assertEqual(profiles.delete_photo(self.profile_id, self.session).status_code, 204)
        self.assertIsNone(profiles.list_profiles(self.session)['items'][0]['photo'])
        with self.assertRaises(HTTPException):
            profiles.download_photo(self.profile_id, self.session)
        self.upload()
        profiles.delete_profile(self.profile_id, self.session)
        self.assertIsNone(self.session.get(models.ProfilePhoto, self.profile_id))
        with self.assertRaises(HTTPException) as error:
            self.upload(profile_id=99999)
        self.assertEqual(error.exception.status_code, 404)

    def test_upload_stops_reading_when_stream_exceeds_limit(self):
        self.assertTrue(callable(getattr(profiles, 'upload_photo', None)), 'Profile photo upload is missing')
        chunks = iter([b'x' * (2 * 1024 * 1024), b'x'])
        async def receive():
            return {'type': 'http.request', 'body': next(chunks), 'more_body': True}
        request = Request({'type': 'http', 'method': 'PUT', 'headers': []}, receive)
        with self.assertRaises(HTTPException) as error:
            asyncio.run(profiles.upload_photo(self.profile_id, request, filename='portrait.png', session=self.session))
        self.assertEqual(error.exception.status_code, 413)

    def test_upload_handles_another_tab_creating_the_photo_after_an_empty_read(self):
        profile = self.session.get(models.UserProfile, self.profile_id)
        self.assertIsNone(profile.photo)
        with Session(self.engine) as other_session:
            profiles.store_profile_photo(other_session, self.profile_id, 'other-tab.png', self.png)
        # This tab still has a cached empty relationship when its upload arrives.
        self.assertIsNone(profile.photo)
        self.upload(filename='this-tab.png')
        self.assertEqual(profiles.list_profiles(self.session)['items'][0]['photo']['filename'], 'this-tab.png')
        self.assertEqual(profiles.download_photo(self.profile_id, self.session).body, self.png)


class PhotoMigrationTests(unittest.TestCase):
    def test_migration_round_trip_preserves_profiles(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.migrations.versions.0032_profile_photo'), 'Profile photo migration is missing')
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0032_profile_photo')
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE user_profiles (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('INSERT INTO user_profiles VALUES (1)')
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertIn('profile_photos', inspect(connection).get_table_names())
                migration.downgrade()
                self.assertNotIn('profile_photos', inspect(connection).get_table_names())
            self.assertEqual(connection.exec_driver_sql('SELECT id FROM user_profiles').scalar(), 1)
        engine.dispose()
