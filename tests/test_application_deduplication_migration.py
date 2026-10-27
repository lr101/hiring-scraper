"""Historical Journey cleanup preserves decisions, source data, and rollback."""
import importlib
import importlib.util
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session

from hiring_scraper.app import application_deduplication, job_search, models, profiles
from hiring_scraper.app.database import initialize_sqlite_schema


MIGRATION = 'hiring_scraper.app.migrations.versions.0032_application_deduplication'
ARCHIVE = 'job_application_deduplication_archive'


class ApplicationDeduplicationMigrationTests(unittest.TestCase):
    def make_engine(self):
        return create_engine('sqlite://')

    def setUp(self):
        self.engine = self.make_engine()
        models.Base.metadata.create_all(self.engine, tables=[table for table in models.Base.metadata.sorted_tables
                                                          if table.name not in {ARCHIVE, 'semantic_search_documents'}])
        with Session(self.engine) as session:
            profile = models.UserProfile(name='Migration demo', preferences={'matching_defaults': {'min_match_score': 0}})
            other = models.UserProfile(name='Independent profile', preferences={'matching_defaults': {'min_match_score': 0}})
            feed = models.JobFeed(company=models.Company(source='test', source_id='dedup', name='Employer'),
                                  provider='html_jobs', feed_url='https://example.test/jobs', status='parsed')
            jobs = [models.Job(feed=feed, external_id=str(index), title='Software engineer', is_remote=True,
                url=f'https://example.test/role?utm_source={index}' if index < 3 else 'https://example.test/distinct')
                for index in range(4)]
            session.add_all([profile, other, feed])
            session.flush()
            self.profile_id, self.other_id = profile.id, other.id
            self.job_ids = [job.id for job in jobs]
            rows = [models.JobApplication(profile_id=profile.id, job_id=jobs[0].id,
                        status='open', previous_status='new'),
                    models.JobApplication(profile_id=profile.id, job_id=jobs[1].id,
                        status='interview', previous_status='waiting_for_reply'),
                    models.JobApplication(profile_id=profile.id, job_id=jobs[2].id,
                        status='new', auto_created=True),
                    models.JobApplication(profile_id=profile.id, job_id=jobs[3].id, status='new'),
                    models.JobApplication(profile_id=other.id, job_id=jobs[0].id, status='not_interested')]
            session.add_all(rows)
            session.add(models.ApplicationMomentumEvent(profile_id=profile.id, job_id=jobs[0].id,
                event_type='applied'))
            session.commit()
            self.saved_states = {(row.profile_id, row.job_id): (row.status, row.previous_status,
                row.auto_created, row.created_at, row.updated_at) for row in rows}

    def tearDown(self):
        self.engine.dispose()

    def migration(self, action):
        self.assertIsNotNone(importlib.util.find_spec(MIGRATION), 'Historical duplicate cleanup needs a migration')
        migration = importlib.import_module(MIGRATION)
        with self.engine.begin() as connection, patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
            getattr(migration, action)()

    def assert_cleaned(self, momentum_events=1):
        with Session(self.engine) as session:
            states = {(row.profile_id, row.job_id): (row.status, row.previous_status)
                      for row in session.scalars(select(models.JobApplication))}
            self.assertEqual(states, {
                (self.profile_id, self.job_ids[1]): ('interview', 'waiting_for_reply'),
                (self.profile_id, self.job_ids[3]): ('new', None),
                (self.other_id, self.job_ids[0]): ('not_interested', None),
            })
            self.assertEqual(session.scalar(select(func.count()).select_from(models.Job)), 4)
            self.assertEqual(session.scalar(select(func.count()).select_from(models.ApplicationMomentumEvent)), momentum_events)
            self.assertEqual(session.execute(text(f'SELECT COUNT(*) FROM {ARCHIVE}')).scalar_one(), 2)

    def remove_vacancy_identity_schema(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE job_vacancy_identities')
            operations = Operations(MigrationContext.configure(connection))
            operations.drop_index('ix_jobs_vacancy_group_id', table_name='jobs')
            with operations.batch_alter_table('jobs', recreate='always') as batch:
                batch.drop_column('vacancy_group_id')

    def test_upgrade_consolidates_existing_duplicates_and_archives_prior_states(self):
        self.migration('upgrade')
        self.assert_cleaned()
        self.migration('upgrade')
        self.assert_cleaned()
        with self.engine.connect() as connection:
            rows = connection.execute(text(f'SELECT job_id, status, previous_status FROM {ARCHIVE}')).all()
        self.assertEqual(set(rows), {(self.job_ids[0], 'open', 'new'), (self.job_ids[2], 'new', None)})

    def test_downgrade_restores_removed_states_and_timestamps(self):
        self.migration('upgrade')
        self.migration('downgrade')
        with Session(self.engine) as session:
            states = {(row.profile_id, row.job_id): (row.status, row.previous_status,
                row.auto_created, row.created_at, row.updated_at)
                for row in session.scalars(select(models.JobApplication))}
        self.assertEqual(states, self.saved_states)

    def test_sqlite_startup_cleans_existing_rows_only_once(self):
        initialize_sqlite_schema(self.engine)
        self.assert_cleaned(momentum_events=3)  # Existing SQLite startup backfills legacy interview events.
        initialize_sqlite_schema(self.engine)
        self.assert_cleaned(momentum_events=3)

    def test_sqlite_startup_adds_identity_schema_to_an_existing_database(self):
        self.remove_vacancy_identity_schema()
        initialize_sqlite_schema(self.engine)
        with self.engine.connect() as connection:
            columns = {column['name'] for column in connection.dialect.get_columns(connection, 'jobs')}
            tables = set(connection.dialect.get_table_names(connection))
        self.assertIn('vacancy_group_id', columns)
        self.assertIn('job_vacancy_identities', tables)
        self.assert_cleaned(momentum_events=3)

    def test_alembic_upgrade_adds_identity_schema_before_cleanup(self):
        self.remove_vacancy_identity_schema()
        self.migration('upgrade')
        with self.engine.connect() as connection:
            columns = {column['name'] for column in connection.dialect.get_columns(connection, 'jobs')}
            tables = set(connection.dialect.get_table_names(connection))
        self.assertIn('vacancy_group_id', columns)
        self.assertIn('job_vacancy_identities', tables)
        self.assert_cleaned()

    def test_sqlite_schema_creation_does_not_skip_data_cleanup(self):
        models.Base.metadata.create_all(self.engine)
        initialize_sqlite_schema(self.engine)
        self.assert_cleaned(momentum_events=3)

    def test_failed_cleanup_rolls_back_and_startup_retries(self):
        original = application_deduplication.consolidate_application_duplicates

        def interrupted(session, **kwargs):
            original(session, **kwargs)
            raise RuntimeError('Interrupted cleanup')

        with patch.object(application_deduplication, 'consolidate_application_duplicates', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'Interrupted cleanup'):
                initialize_sqlite_schema(self.engine)
        with Session(self.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(models.JobApplication)), 5)
            self.assertIsNone(session.get(models.AppDataMigration, application_deduplication.MIGRATION_KEY))
        initialize_sqlite_schema(self.engine)
        self.assert_cleaned(momentum_events=3)

    def test_downgrade_does_not_overwrite_later_saved_decisions(self):
        self.migration('upgrade')
        with Session(self.engine) as session:
            session.add(models.JobApplication(profile_id=self.profile_id, job_id=self.job_ids[0], status='accepted'))
            session.commit()
        self.migration('downgrade')
        with Session(self.engine) as session:
            saved = session.scalar(select(models.JobApplication).where(
                models.JobApplication.profile_id == self.profile_id, models.JobApplication.job_id == self.job_ids[0]))
            self.assertEqual(saved.status, 'accepted')

    def test_deleting_profile_removes_its_archived_history(self):
        self.migration('upgrade')
        with Session(self.engine) as session:
            profiles.delete_profile(self.profile_id, session)
            self.assertEqual(session.scalar(select(func.count()).select_from(models.JobApplicationDeduplicationArchive)), 0)

    def test_match_rebuild_and_incremental_refresh_do_not_recreate_automatic_duplicates(self):
        # Match publication must also consolidate databases without a historical cleanup.
        models.JobApplicationDeduplicationArchive.__table__.create(self.engine, checkfirst=True)
        with Session(self.engine) as session:
            profiles = session.scalars(select(models.UserProfile)).all()
            job_search.ensure_profile_match_indexes(session, profiles)
            session.commit()
            self.assertEqual(session.scalar(select(func.count()).select_from(models.JobApplication).where(
                models.JobApplication.profile_id == self.profile_id)), 3)  # two manual histories and one distinct role
            job_search.refresh_application_matches_for_jobs(session, self.job_ids)
            session.commit()
            self.assertEqual(session.scalar(select(func.count()).select_from(models.JobApplication).where(
                models.JobApplication.profile_id == self.profile_id)), 3)
        self.migration('upgrade')
        with Session(self.engine) as session:
            job_search.refresh_application_matches_for_jobs(session, self.job_ids)
            session.commit()
            self.assertEqual(session.scalar(select(func.count()).select_from(models.JobApplication).where(
                models.JobApplication.profile_id == self.profile_id)), 2)

    def test_removed_bridge_identity_still_groups_a_later_incremental_match(self):
        with Session(self.engine) as session:
            bridge = session.get(models.Job, self.job_ids[2])
            winner = session.get(models.Job, self.job_ids[1])
            winner.url = 'https://example.test/translated'
            winner.feed.provider = 'greenhouse'
            winner.raw_metadata = bridge.raw_metadata = {'requisition_id': 'bridge'}
            session.commit()
        self.migration('upgrade')
        with Session(self.engine) as session:
            job_search.refresh_application_matches_for_jobs(session, [self.job_ids[0]])
            session.commit()
            self.assertEqual(session.scalar(select(func.count()).select_from(models.JobApplication).where(
                models.JobApplication.profile_id == self.profile_id)), 2)
            self.assertEqual(len(application_deduplication.canonical_application_states(session, [self.profile_id])), 2)

    def test_one_job_refresh_does_not_load_unrelated_journey_jobs(self):
        self.migration('upgrade')
        with Session(self.engine) as session:
            feed_id = session.get(models.Job, self.job_ids[0]).feed_id
            jobs = [models.Job(feed_id=feed_id, external_id=f'unrelated-{index}', title='Unrelated role',
                               url=f'https://example.test/unrelated/{index}', is_remote=True)
                    for index in range(300)]
            session.add_all(jobs)
            session.flush()
            session.add_all(models.JobApplication(profile_id=self.profile_id, job_id=job.id, status='open')
                            for job in jobs)
            session.commit()
            job_search.ensure_profile_match_indexes(session, session.scalars(select(models.UserProfile)).all())
            session.commit()
        with Session(self.engine) as session:
            loaded_jobs = []
            event.listen(session, 'loaded_as_persistent', lambda _session, obj:
                         loaded_jobs.append(obj.id) if isinstance(obj, models.Job) else None)
            job_search.refresh_application_matches_for_jobs(session, [self.job_ids[0]])
            self.assertLessEqual(len(set(loaded_jobs)), 5, 'A one-job refresh must not scan unrelated job histories')
