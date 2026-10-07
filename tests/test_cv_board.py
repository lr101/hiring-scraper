"""Explicit CV board imports must not duplicate profiles or silently seed them."""
import json
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from hiring_scraper.app.models import Base, UserProfile, Job


class CVBoardImportTests(unittest.TestCase):
    def test_explicit_import_reuses_profile_and_updates_verified_preferences(self):
        from hiring_scraper.app.cv_board import import_board
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with tempfile.TemporaryDirectory() as directory, Session(engine) as session:
            profile = Path(directory) / 'profile.json'
            snapshot = Path(directory) / 'jobs.json'
            profile.write_text(json.dumps({'name': 'Anonymous project profile', 'skills': ['CAD'],
                'language_levels': {'German': 'B2'}, 'skill_evidence': {'CAD': {'context': 'academic', 'note': 'University project'}},
                'search_area': {'city': 'Heidelberg', 'latitude': 49.40936, 'longitude': 8.69472, 'radius_km': 35, 'country_code': 'DE'}}))
            snapshot.write_text(json.dumps([{'refnr': 'test-project', 'titel': 'Projektkoordinator', 'arbeitgeber': 'Example employer',
                'arbeitsorte': [{'ort': 'Heidelberg', 'land': 'Deutschland', 'koordinaten': {'lat': 49.41, 'lon': 8.69}}]}]))
            first = import_board(session, profile, snapshot)
            data = json.loads(profile.read_text())
            data['language_levels']['German'] = 'C1'
            profile.write_text(json.dumps(data))
            second = import_board(session, profile, snapshot)
            self.assertEqual(first['profile_id'], second['profile_id'])
            self.assertEqual(session.scalar(select(func.count()).select_from(UserProfile)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)
            saved = session.get(UserProfile, first['profile_id'])
            self.assertEqual(saved.preferences['language_levels']['German'], 'C1')
            self.assertEqual(saved.preferences['skill_evidence']['CAD']['context'], 'academic')
            self.assertEqual(saved.preferences['search_area']['radius_km'], 35)

    def test_invalid_profile_does_not_import_jobs(self):
        from hiring_scraper.app.cv_board import import_board
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with tempfile.TemporaryDirectory() as directory, Session(engine) as session:
            path = Path(directory) / 'profile.json'
            path.write_text(json.dumps({'name': 'Invalid profile', 'language_levels': {'German': 'fluent'}}))
            with self.assertRaises(ValueError):
                import_board(session, path, [])
            self.assertEqual(session.scalar(select(func.count()).select_from(UserProfile)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 0)

    def test_employer_supplement_is_idempotent_and_keeps_source_expiry(self):
        from hiring_scraper.app.cv_board import import_board
        from hiring_scraper.app.models import JobFeed
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with tempfile.TemporaryDirectory() as directory, Session(engine) as session:
            profile = Path(directory) / 'profile.json'
            supplement = Path(directory) / 'employers.json'
            profile.write_text(json.dumps({'name': 'Anonymous project profile'}))
            supplement.write_text(json.dumps({'observed_at': '2026-10-07T06:00:00Z', 'companies': [{
                'source_id': 'example-employer', 'name': 'Example employer', 'website_url': 'https://example.test',
                'feeds': [{'provider': 'schema_org', 'feed_url': 'https://example.test/jobs', 'jobs': [{
                    'id': 'role-1', 'title': 'Project coordinator', 'url': 'https://example.test/jobs/role-1',
                    'description': 'Project coordination and requirements gathering.', 'location': 'Heidelberg',
                    'locations': [{'label': 'Heidelberg', 'latitude': 49.41, 'longitude': 8.69, 'country_code': 'de'}],
                    'raw_metadata': {'validThrough': '2026-10-06'}}]}]}]}))
            import_board(session, profile, [], supplement)
            session.commit()
            import_board(session, profile, [], supplement)
            session.commit()
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)
            job = session.scalar(select(Job))
            self.assertEqual(job.raw_metadata['validThrough'], '2026-10-06')
            self.assertEqual(len(job.locations), 1)
            self.assertIsNone(session.scalar(select(JobFeed)).next_scan_at)
