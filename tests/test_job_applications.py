"""Application tracking persists independently of vacancy and matching lifecycles."""
import unittest
import importlib
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from hiring_scraper.app import api, job_search, models, profiles
from hiring_scraper.app.database import initialize_sqlite_schema


class JobApplicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.profile = profiles.create_profile(profiles.ProfileRequest(name='Developer'), self.session)
        self.other = profiles.create_profile(profiles.ProfileRequest(name='Designer'), self.session)
        company = models.Company(source='test', source_id='employer', name='Employer')
        feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.test/jobs', status='parsed')
        self.jobs = [models.Job(feed=feed, external_id=str(index), title=f'Role {index}',
                               url=f'https://example.test/jobs/{index}', is_remote=True)
                     for index in range(8)]
        self.session.add(company)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def save(self, job, status='new', profile=None):
        endpoint = getattr(api, 'set_job_application', None)
        self.assertIsNotNone(endpoint, 'Jobs must support saving application status')
        return endpoint((profile or self.profile)['id'], job.id,
                        api.ApplicationRequest(status=status), self.session)

    def overview(self, profile=None, **kwargs):
        endpoint = getattr(api, 'list_job_applications', None)
        self.assertIsNotNone(endpoint, 'Profiles must expose an application overview')
        return endpoint((profile or self.profile)['id'], session=self.session, **kwargs)

    def test_production_journey_returns_saved_roles_before_rebuilding_matches(self):
        self.save(self.jobs[0], 'open')
        with patch.object(api, 'IS_SQLITE', False):
            page = api.list_applications(profile_ids=str(self.profile['id']),
                                         statuses='all', session=self.session)
        self.assertEqual([item['id'] for item in page['items']], [self.jobs[0].id])
        self.assertEqual(page['counts']['new'], 0)
        self.assertTrue(page['matching_pending'])
        self.assertIsNone(self.session.get(models.UserProfile, self.profile['id']).match_index_version)

    def test_background_rebuild_populates_journey_and_preserves_saved_stages(self):
        self.save(self.jobs[0], 'interview')
        refresh = job_search.refresh_profile_match_indexes_once
        with patch.object(job_search, 'SessionLocal', sessionmaker(bind=self.engine)):
            self.assertTrue(refresh())
            # The other saved profile is also stale and is processed next.
            self.assertTrue(refresh())
            self.assertFalse(refresh())
        self.session.expire_all()
        with patch.object(api, 'IS_SQLITE', False):
            page = api.list_applications(profile_ids=str(self.profile['id']),
                                         statuses='all', session=self.session)
        self.assertFalse(page['matching_pending'])
        self.assertEqual(page['counts']['interview'], 1)
        self.assertEqual(page['counts']['new'], 7)
        self.assertEqual(page['total'], 8)

    def test_every_status_roundtrips_without_creating_duplicate_applications(self):
        for status in ['new', 'open', 'in_gots_hand', 'not_interested', 'waiting_for_reply', 'automated_application', 'interview', 'rejected', 'accepted', 'closed']:
            with self.subTest(status=status):
                saved = self.save(self.jobs[0], status)
                self.session.expire_all()
                self.assertEqual(saved['status'], status)
                self.assertEqual(saved['job_id'], self.jobs[0].id)
                self.assertEqual(saved['profile_id'], self.profile['id'])
                self.assertTrue(saved['updated_at'])
                page = self.overview()
                self.assertEqual(page['total'], 1)
                self.assertEqual(page['items'][0]['application']['status'], status)
                self.assertEqual(page['counts'][status], 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 1)

    def test_profiles_track_the_same_job_independently(self):
        self.save(self.jobs[0], 'interview')
        self.save(self.jobs[0], 'not_interested', self.other)
        self.assertEqual(self.overview()['items'][0]['application']['status'], 'interview')
        self.assertEqual(self.overview(self.other)['items'][0]['application']['status'], 'not_interested')
        self.save(self.jobs[1], 'open', self.other)
        self.assertEqual(self.overview()['total'], 1)

    def test_filtering_precedes_pagination_and_counts_include_all_statuses(self):
        self.save(self.jobs[0], 'new')
        self.save(self.jobs[1], 'interview')
        self.save(self.jobs[2], 'interview')
        page = self.overview(status='interview', offset=1, limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(page['items'][0]['application']['status'], 'interview')
        self.assertEqual(page['counts'], {'new': 1, 'open': 0, 'not_interested': 0,
                         'in_gots_hand': 0, 'automated_application': 0,
                         'waiting_for_reply': 0, 'interview': 2, 'rejected': 0, 'accepted': 0, 'closed': 0})

    def test_parallel_stages_filter_independently_and_keep_undo_history(self):
        self.save(self.jobs[0], 'new')
        held = self.save(self.jobs[0], 'in_gots_hand')
        self.assertEqual(held['previous_status'], 'new')
        self.save(self.jobs[1], 'open')
        held = self.save(self.jobs[1], 'in_gots_hand')
        self.assertEqual(held['previous_status'], 'open')
        automated = self.save(self.jobs[0], 'automated_application')
        self.assertEqual(automated['previous_status'], 'in_gots_hand')
        self.assertEqual(self.overview(status='in_gots_hand')['total'], 1)
        page = api.list_applications(profile_ids=str(self.profile['id']),
                                     statuses='automated_application', session=self.session)
        self.assertEqual([item['id'] for item in page['items']], [self.jobs[0].id])
        self.assertEqual(page['counts']['in_gots_hand'], 1)
        restored = api.set_job_application(self.profile['id'], self.jobs[0].id,
            api.ApplicationRequest(status='in_gots_hand', action='undo'), self.session)
        self.assertEqual(restored['status'], 'in_gots_hand')
        self.assertEqual(self.overview(status='in_gots_hand')['total'], 2)

    def test_sent_autofill_applications_count_once_across_both_sent_stages(self):
        self.save(self.jobs[0], 'in_gots_hand')
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 0)
        self.save(self.jobs[0], 'automated_application')
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 1)
        self.save(self.jobs[0], 'waiting_for_reply')
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 1)
        self.assertEqual(api.get_application_momentum(self.session)['applied_this_week'], 1)
        self.save(self.jobs[0], 'automated_application')
        self.save(self.jobs[0], 'waiting_for_reply')
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 1)

    def test_closed_expired_and_out_of_scope_applications_remain_in_overview(self):
        self.save(self.jobs[0], 'waiting_for_reply')
        self.jobs[0].is_active = False
        self.jobs[0].raw_metadata = {'validThrough': '2000-01-01'}
        self.jobs[0].is_remote = False
        self.session.commit()
        page = self.overview()
        self.assertEqual(page['total'], 1)
        self.assertFalse(page['items'][0]['is_active'])
        self.assertTrue(page['items'][0]['expired'])
        self.assertEqual(page['items'][0]['company_name'], 'Employer')

    def test_tracking_is_visible_in_board_and_detail_without_changing_vacancy_status(self):
        before = api.get_job(self.jobs[0].id, self.session, profile_id=self.profile['id'])
        self.assertIsNone(before.get('application'))
        self.save(self.jobs[0], 'rejected')
        detail = api.get_job(self.jobs[0].id, self.session, profile_id=self.profile['id'])
        board = api.list_jobs(49, 8, 35, None, None, None, 0, 100,
                              session=self.session, profile_id=self.profile['id'])
        item = next(item for item in board['items'] if item['id'] == self.jobs[0].id)
        self.assertEqual(detail['application'], item['application'])
        self.assertEqual(detail['application']['status'], 'rejected')
        self.assertTrue(detail['is_active'])
        self.assertIsNone(api.get_job(self.jobs[0].id, self.session, profile_id=self.other['id']).get('application'))

    def test_untracked_jobs_are_not_silently_added_to_overview(self):
        page = self.overview()
        self.assertEqual(page['total'], 0)
        self.assertEqual(page['items'], [])
        self.assertEqual(sum(page['counts'].values()), 0)

    def test_invalid_status_and_missing_resources_are_rejected(self):
        self.assertIsNotNone(getattr(api, 'ApplicationRequest', None), 'Status payload must be validated')
        for value in ['applied', '', None, 'NEW']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                api.ApplicationRequest(status=value)
        for profile_id, job_id in [(99999, self.jobs[0].id), (self.profile['id'], 99999)]:
            with self.subTest(profile_id=profile_id), self.assertRaises(HTTPException) as error:
                api.set_job_application(profile_id, job_id, api.ApplicationRequest(status='open'), self.session)
            self.assertEqual(error.exception.status_code, 404)
        with self.assertRaises(HTTPException) as error:
            self.overview(profile={'id': 99999})
        self.assertEqual(error.exception.status_code, 404)

    def test_deleting_profile_cleans_up_tracking_but_preserves_job(self):
        self.save(self.jobs[0], 'accepted')
        profiles.delete_profile(self.profile['id'], self.session)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 0)
        self.assertIsNotNone(self.session.get(models.Job, self.jobs[0].id))

    def test_sqlite_startup_adds_tracking_to_existing_database_idempotently(self):
        engine = create_engine('sqlite://')
        try:
            models.Base.metadata.create_all(engine, tables=[table for table in models.Base.metadata.sorted_tables
                                                          if table.name != 'job_applications'])
            initialize_sqlite_schema(engine)
            initialize_sqlite_schema(engine)
            with Session(engine) as session:
                endpoint = getattr(api, 'list_job_applications', None)
                self.assertIsNotNone(endpoint, 'SQLite startup must make application tracking available')
                profile = profiles.create_profile(profiles.ProfileRequest(name='Existing database'), session)
                self.assertEqual(endpoint(profile['id'], session=session)['total'], 0)
        finally:
            engine.dispose()

    def test_new_tracking_of_an_inactive_job_uses_closed_stage(self):
        self.jobs[0].is_active = False
        self.session.commit()
        saved = self.save(self.jobs[0], 'new')
        self.assertEqual(saved['status'], 'closed')
        page = api.list_applications(profile_ids=str(self.profile['id']), statuses='closed', session=self.session)
        self.assertEqual([item['id'] for item in page['items']], [self.jobs[0].id])

    def test_inactive_jobs_cannot_reenter_active_journey_stages(self):
        targets = {'new': 'closed', 'open': 'closed', 'in_gots_hand': 'closed',
                   'not_interested': 'closed', 'interview': 'closed', 'closed': 'closed',
                   'waiting_for_reply': 'rejected', 'automated_application': 'rejected'}
        for job, (requested, expected) in zip(self.jobs, targets.items()):
            job.is_active = False
            self.session.commit()
            self.assertEqual(self.save(job, requested)['status'], expected)
        self.assertEqual(api.get_application_momentum(self.session)['lifetime_applied'], 2)

    def test_stale_new_tracking_preserves_closed_jobs_completed_outcomes(self):
        for job, status in zip(self.jobs, ['accepted', 'rejected']):
            self.save(job, status)
            job.is_active = False
            self.session.commit()
            self.assertEqual(self.save(job, 'new')['status'], status)

    def legacy_application_table(self):
        for job in self.jobs:
            self.session.refresh(job)
        self.session.close()
        with self.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE job_applications')
            connection.exec_driver_sql("""CREATE TABLE job_applications (
                id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                status VARCHAR(24) NOT NULL, previous_status VARCHAR(24),
                auto_created BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT uq_job_application_profile_job UNIQUE (profile_id, job_id),
                CONSTRAINT ck_job_application_status CHECK (status IN
                    ('new','open','not_interested','waiting_for_reply','interview','rejected','accepted'))
            )""")
            connection.exec_driver_sql('CREATE INDEX ix_job_applications_profile_status ON job_applications (profile_id,status)')
            connection.exec_driver_sql('CREATE INDEX ix_job_applications_job_id ON job_applications (job_id)')
        self.session = Session(self.engine)

    def test_sqlite_upgrade_preserves_old_applications_and_accepts_parallel_stages(self):
        self.legacy_application_table()
        old = self.save(self.jobs[0], 'open')
        initialize_sqlite_schema(self.engine)
        initialize_sqlite_schema(self.engine)
        self.assertEqual(self.overview()['items'][0]['application'], old)
        self.assertEqual(self.save(self.jobs[0], 'in_gots_hand')['previous_status'], 'open')
        self.assertEqual(self.save(self.jobs[1], 'automated_application')['status'], 'automated_application')

    def closed_stage_upgrade_fixture(self):
        self.legacy_application_table()
        previous_migration = importlib.import_module('hiring_scraper.app.migrations.versions.0029_parallel_application_stages')
        with self.engine.begin() as connection, patch.object(previous_migration, 'op', Operations(MigrationContext.configure(connection))):
            previous_migration.upgrade()
        for job, status in zip(self.jobs[:5], ['waiting_for_reply', 'open', 'accepted', 'rejected', 'automated_application']):
            self.save(job, status)
            self.session.get(models.Job, job.id).is_active = False
        self.session.commit()

    def assert_closed_stage_upgrade(self):
        self.session.expire_all()
        states = {row.job_id: (row.status, row.previous_status) for row in self.session.scalars(select(models.JobApplication))}
        self.assertEqual(states, {
            self.jobs[0].id: ('rejected', 'waiting_for_reply'), self.jobs[1].id: ('closed', 'open'),
            self.jobs[2].id: ('accepted', None), self.jobs[3].id: ('rejected', None),
            self.jobs[4].id: ('rejected', 'automated_application')})

    def test_sqlite_closed_stage_upgrade_backfills_existing_closed_jobs_once(self):
        self.closed_stage_upgrade_fixture()
        initialize_sqlite_schema(self.engine)
        self.assert_closed_stage_upgrade()
        initialize_sqlite_schema(self.engine)
        self.assert_closed_stage_upgrade()

    def test_alembic_closed_stage_upgrade_and_downgrade_preserve_tracking(self):
        self.closed_stage_upgrade_fixture()
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0030_closed_application_stage')
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            migration.upgrade()
        self.assert_closed_stage_upgrade()
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            migration.downgrade()
        self.session.expire_all()
        row = self.session.scalar(select(models.JobApplication).where(models.JobApplication.job_id == self.jobs[1].id))
        self.assertEqual(row.status, 'open')

    def test_alembic_upgrade_and_downgrade_preserve_parallel_stage_records(self):
        self.legacy_application_table()
        self.save(self.jobs[0], 'open')
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0029_parallel_application_stages')
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            migration.upgrade()
        self.save(self.jobs[0], 'in_gots_hand')
        self.save(self.jobs[0], 'automated_application')
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            migration.downgrade()
        self.session.expire_all()
        row = self.session.scalar(select(models.JobApplication))
        self.assertEqual((row.status, row.previous_status), ('waiting_for_reply', 'open'))
