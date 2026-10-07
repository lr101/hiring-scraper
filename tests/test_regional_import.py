import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from hiring_scraper.app.models import Base, Company, Job, JobFeed
from hiring_scraper.app.regional import import_snapshot, load_search_area
from hiring_scraper.regional import normalize_job

ROW = {'referenznummer': '123-A-S', 'stellenangebotsTitel': 'Project coordinator',
       'firma': 'Example GmbH', 'arbeitgeberKundennummerHash': 'employer-hash',
       'stellenangebotsBeschreibung': 'Tasks\nCoordinate delivery\n\nRequirements\nGerman B2',
       'stellenlokationen': [{'adresse': {'ort': 'Heidelberg', 'land': 'DEUTSCHLAND'}, 'breite': 49.4, 'laenge': 8.7}]}


class RegionalImportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_raw_and_normalized_import_are_idempotent_and_feeds_never_enter_ats_lifecycle(self):
        counts = import_snapshot(self.session, [ROW])
        self.assertEqual(counts['jobs_created'], 1)
        import_snapshot(self.session, {'jobs': [normalize_job(ROW)]})
        self.assertEqual(len(self.session.scalars(select(Company)).all()), 1)
        self.assertEqual(len(self.session.scalars(select(Job)).all()), 1)
        self.assertEqual(len(self.session.scalars(select(JobFeed)).all()), 1)
        feed = self.session.scalar(select(JobFeed))
        self.assertIsNone(feed.next_scan_at)
        self.assertEqual(feed.provider, 'arbeitsagentur')
        self.assertEqual(feed.job_count, 1)
        self.assertEqual(self.session.scalar(select(Job)).description, ROW['stellenangebotsBeschreibung'])

    def test_failed_sparse_refresh_preserves_full_description_source_evidence_and_other_open_jobs(self):
        import_snapshot(self.session, [ROW, {**ROW, 'referenznummer': 'other'}])
        self.session.flush()
        first = self.session.scalar(select(Job).where(Job.external_id == '123-A-S'))
        first.missing_complete_scans = 2
        import_snapshot(self.session, [{**ROW, 'stellenangebotsBeschreibung': '', 'stellenlokationen': [], '_detail_state': 'http_error'}])
        self.assertEqual(first.description, ROW['stellenangebotsBeschreibung'])
        self.assertEqual(first.raw_metadata['source_record']['stellenangebotsBeschreibung'], ROW['stellenangebotsBeschreibung'])
        self.assertEqual(first.locations[0].latitude, 49.4)
        self.assertEqual(first.missing_complete_scans, 2)
        self.assertTrue(self.session.scalar(select(Job).where(Job.external_id == 'other')).is_active)
        self.assertIsNone(self.session.scalar(select(Job).where(Job.external_id == 'other')).closed_at)

    def test_reference_is_scoped_to_employer_and_hash_survives_company_rename(self):
        import_snapshot(self.session, [ROW, {**ROW, 'firma': 'Other employer', 'arbeitgeberKundennummerHash': 'other-hash'}])
        import_snapshot(self.session, [{**ROW, 'firma': 'Example renamed GmbH'}])
        self.assertEqual(len(self.session.scalars(select(Company)).all()), 2)
        self.assertEqual(len(self.session.scalars(select(Job)).all()), 2)
        self.assertEqual(self.session.scalar(select(Company).where(Company.source_id == 'hash:employer-hash')).name, 'Example renamed GmbH')

    def test_import_path_skips_malformed_rows_and_saved_area_uses_preferences(self):
        from hiring_scraper.app.models import UserProfile
        profile = UserProfile(name='CV', preferences={'search_area': {'city': 'Heidelberg', 'radius_km': 35}})
        self.session.add(profile)
        self.session.flush()
        self.assertEqual(load_search_area(self.session, profile_id=profile.id), {'city': 'Heidelberg', 'radius_km': 35})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'details.json'
            path.write_text(json.dumps([ROW, {}, None]))
            counts = import_snapshot(self.session, path)
            self.assertEqual(counts['skipped'], 2)
            self.assertEqual(counts['jobs_created'], 1)


if __name__ == '__main__':
    unittest.main()

class RegionalEvidenceRefreshTests(unittest.TestCase):
    def test_sparse_refresh_preserves_agency_and_salary_evidence(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            import_snapshot(session, [{**ROW, 'istPrivateArbeitsvermittlung': True, 'festgehalt': 50000,
                                       'verguetungsangabe': 'JAHRESGEHALT'}])
            sparse = {key: value for key, value in ROW.items() if key != 'stellenangebotsBeschreibung'}
            import_snapshot(session, [sparse])
            job = session.scalar(select(Job))
            self.assertEqual(job.raw_metadata['employer_type'], 'agency')
            self.assertTrue(job.raw_metadata['is_private_agency'])
            self.assertEqual(job.raw_metadata['salary_evidence']['festgehalt'], 50000)
        engine.dispose()

class RegionalSparseIdentityTests(unittest.TestCase):
    def test_same_employer_reference_remains_one_job_when_sparse_refresh_omits_hash(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            import_snapshot(session, [ROW])
            sparse = {key: value for key, value in ROW.items() if key != 'arbeitgeberKundennummerHash'}
            counts = import_snapshot(session, [sparse])
            self.assertEqual(counts['jobs_created'], 0)
            self.assertEqual(session.scalar(select(func.count(Job.id))), 1)
        engine.dispose()

class RegionalVerifiedFieldRefreshTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.full = {**ROW, 'homeofficemoeglich': True, 'homeofficetyp': 'ANGABE_IN_PROZENT',
                     'homeofficeprozent': 100, '_checked_at': '2026-10-01T06:00:00Z',
                     '_detail_url': 'https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/MTIz', '_detail_state': 'ok'}
        import_snapshot(self.session, [self.full])
    def tearDown(self):
        self.session.close()
        self.engine.dispose()
    def test_failed_sparse_refresh_retains_explicit_remote_and_coordinates_with_original_provenance(self):
        sparse = {key: value for key, value in ROW.items() if key != 'stellenangebotsBeschreibung'}
        sparse.update(homeofficemoeglich=True, _detail_state='http_error', _checked_at='2026-10-07T06:00:00Z',
                      stellenlokationen=[{'adresse': {'ort': 'Heidelberg', 'land': 'DEUTSCHLAND'}}])
        import_snapshot(self.session, [sparse])
        job = self.session.scalar(select(Job))
        self.assertTrue(job.is_remote)
        self.assertEqual(job.work_arrangement, 'remote')
        self.assertEqual((job.locations[0].latitude, job.locations[0].longitude), (49.4, 8.7))
        evidence = job.raw_metadata['field_evidence']
        self.assertEqual(evidence['work_arrangement']['source_observed_at'], '2026-10-01T06:00:00Z')
        self.assertEqual(evidence['locations'][0]['source_observed_at'], '2026-10-01T06:00:00Z')
        self.assertEqual(evidence['locations'][0]['source_api_url'], self.full['_detail_url'])
        self.assertEqual(job.raw_metadata['source_observed_at'], '2026-10-07T06:00:00Z')
        self.assertEqual(job.raw_metadata['remote_country_codes'], [])

    def test_explicit_nonremote_and_changed_valid_coordinates_replace_previous_facts(self):
        changed = {**ROW, 'homeofficemoeglich': False, '_detail_state': 'ok', '_checked_at': '2026-10-07T06:00:00Z',
                   'stellenlokationen': [{'adresse': {'ort': 'Heidelberg', 'land': 'DEUTSCHLAND'}, 'breite': 49.41, 'laenge': 8.71}]}
        import_snapshot(self.session, [changed])
        job = self.session.scalar(select(Job))
        self.assertFalse(job.is_remote)
        self.assertIsNone(job.work_arrangement)
        self.assertIsNone(job.raw_metadata['homeoffice_percentage'])
        self.assertEqual((job.locations[0].latitude, job.locations[0].longitude), (49.41, 8.71))
        self.assertEqual(job.raw_metadata['field_evidence']['work_arrangement']['source_observed_at'], '2026-10-07T06:00:00Z')

    def test_sparse_new_location_never_inherits_previous_location_coordinates(self):
        changed = {**ROW, '_detail_state': 'http_error',
                   'stellenlokationen': [{'adresse': {'ort': 'Mannheim', 'land': 'DEUTSCHLAND'}}]}
        import_snapshot(self.session, [changed])
        job = self.session.scalar(select(Job))
        self.assertEqual(job.locations[0].label, 'Mannheim')
        self.assertIsNone(job.locations[0].latitude)
        self.assertIsNone(job.locations[0].longitude)
