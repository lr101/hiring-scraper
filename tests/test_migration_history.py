"""Populated databases upgrade through historical data migrations to head."""
import os
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from tests import pg


class HistoricalUpgradeTests(unittest.TestCase):
    def setUp(self):
        base = make_url(pg.url())
        # Same disposable prefix as the test database; dropped after the test.
        self.name = f'{base.database}_history_{uuid4().hex[:8]}'
        self.admin = create_engine(base.set(database='postgres'), isolation_level='AUTOCOMMIT')
        with self.admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{self.name}" TEMPLATE template0'))
        self.addCleanup(self.drop)
        self.target = base.set(database=self.name).render_as_string(hide_password=False)
        self.config = Config()
        self.config.set_main_option('script_location', 'hiring_scraper/app/migrations')
        self.config.set_main_option('sqlalchemy.url', self.target)

    def drop(self):
        with self.admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'))
        self.admin.dispose()

    def test_populated_profile_upgrades_through_deduplication_to_head(self):
        # Migrations import current application helpers; they must not read
        # columns that later revisions add (0033 once selected source_version).
        with patch.dict(os.environ, {'DATABASE_URL': self.target}):
            command.upgrade(self.config, '0032_profile_photo')
            engine = create_engine(self.target)
            try:
                with engine.begin() as connection:
                    connection.execute(text(
                        "INSERT INTO user_profiles (name, preferences, created_at, updated_at) "
                        "VALUES ('Historical', '{}', now(), now())"))
                command.upgrade(self.config, 'head')
                with engine.connect() as connection:
                    self.assertEqual(connection.execute(text(
                        'SELECT name, source_version FROM user_profiles')).one(), ('Historical', 0))
            finally:
                engine.dispose()

    def test_migration_0043_invalidates_legacy_ai_matches_and_preserves_user_stages(self):
        with patch.dict(os.environ, {'DATABASE_URL': self.target}):
            command.upgrade(self.config, '0042_profile_match_explanation')
            engine = create_engine(self.target)
            try:
                with engine.begin() as connection:
                    profile_id = connection.execute(text(
                        "INSERT INTO user_profiles (name, preferences, match_index_version, "
                        "presentation_refresh_pending, created_at, updated_at) "
                        "VALUES ('Migrating', '{}', 'legacy-ai-v1', TRUE, now(), now()) RETURNING id"
                    )).scalar_one()
                    company_id = connection.execute(text(
                        "INSERT INTO companies (source, source_id, name, career_status, discovery_attempt_count) "
                        "VALUES ('manual', 'c1', 'Acme GmbH', 'not_checked', 0) RETURNING id"
                    )).scalar_one()
                    feed_id = connection.execute(text(
                        "INSERT INTO job_feeds (company_id, provider, feed_url, status, enabled, attempt_count) "
                        "VALUES (:company_id, 'rss', 'https://example.com/feed.xml', 'ok', TRUE, 0) RETURNING id"
                    ), {'company_id': company_id}).scalar_one()
                    job_ids = []
                    for idx in range(1, 5):
                        job_ids.append(connection.execute(text(
                            "INSERT INTO jobs (feed_id, external_id, title, url, is_remote, raw_metadata, "
                            "enrichment, first_seen_at, last_seen_at, is_active, missing_complete_scans) "
                            "VALUES (:feed_id, :ext, :title, :url, FALSE, '{}', '{}', now(), now(), TRUE, 0) RETURNING id"
                        ), {
                            'feed_id': feed_id,
                            'ext': f'job-{idx}',
                            'title': f'Engineer {idx}',
                            'url': f'https://example.com/jobs/{idx}',
                        }).scalar_one())
                    connection.execute(text(
                        "CREATE TABLE IF NOT EXISTS ai_matching_observed_config "
                        "(id INTEGER PRIMARY KEY, version VARCHAR(160) NOT NULL, updated_at TIMESTAMPTZ NOT NULL)"
                    ))
                    connection.execute(text(
                        "INSERT INTO profile_llm_scores (profile_id, job_id, source_hash, version, assessment, scored_at) "
                        "VALUES (:pid, :jid, 'h1', 'v1', '{\"score\": 92}', now())"
                    ), {'pid': profile_id, 'jid': job_ids[0]})
                    connection.execute(text(
                        "INSERT INTO profile_job_matches (profile_id, job_id, match_score, match_tier, "
                        "match_uncertain, explanation, score_version) "
                        "VALUES (:pid, :jid, 92, 'recommended', FALSE, "
                        "'{\"score\": 92, \"source\": \"ai\"}'::jsonb, 'ai-v1')"
                    ), {'pid': profile_id, 'jid': job_ids[0]})
                    connection.execute(text(
                        "INSERT INTO profile_match_progress (profile_id, version, cursor, high_water, advanced_at) "
                        "VALUES (:pid, 'legacy-ai-v1', :cursor, :hw, now())"
                    ), {'pid': profile_id, 'cursor': job_ids[0], 'hw': job_ids[-1]})
                    # Untouched automatic 'new' row -> must be cleared for local rebuild.
                    # User-created 'new' and user-set stages -> must all be preserved.
                    for jid, status, auto_created in (
                        (job_ids[0], 'new', True),
                        (job_ids[1], 'new', False),
                        (job_ids[2], 'open', False),
                        (job_ids[3], 'waiting_for_reply', False),
                    ):
                        connection.execute(text(
                            "INSERT INTO job_applications (profile_id, job_id, status, auto_created, created_at, updated_at) "
                            "VALUES (:pid, :jid, :status, :auto_created, now(), now())"
                        ), {'pid': profile_id, 'jid': jid, 'status': status, 'auto_created': auto_created})

                command.upgrade(self.config, 'head')

                with engine.connect() as connection:
                    for dropped in (
                        'ai_matching_observed_config',
                        'ai_matching_workers',
                        'ai_matching_tasks',
                        'ai_matching_settings',
                        'ai_matching_selections',
                        'ai_matching_calls',
                        'profile_llm_scores',
                    ):
                        self.assertIsNone(connection.execute(
                            text('SELECT to_regclass(:name)'), {'name': dropped}).scalar_one())
                    self.assertEqual(connection.execute(text('SELECT count(*) FROM profile_job_matches')).scalar_one(), 0)
                    self.assertEqual(connection.execute(text('SELECT count(*) FROM profile_match_progress')).scalar_one(), 0)
                    self.assertEqual(
                        connection.execute(text(
                            'SELECT match_index_version, presentation_refresh_pending FROM user_profiles WHERE id = :pid'
                        ), {'pid': profile_id}).one(),
                        (None, False),
                    )
                    remaining = connection.execute(text(
                        'SELECT job_id, status, auto_created FROM job_applications WHERE profile_id = :pid ORDER BY job_id'
                    ), {'pid': profile_id}).all()
                    self.assertEqual(remaining, [
                        (job_ids[1], 'new', False),
                        (job_ids[2], 'open', False),
                        (job_ids[3], 'waiting_for_reply', False),
                    ])

                command.downgrade(self.config, '0042_profile_match_explanation')
                with engine.connect() as connection:
                    self.assertIsNotNone(connection.execute(
                        text("SELECT to_regclass('profile_llm_scores')")).scalar_one())
                    self.assertEqual(connection.execute(text(
                        'SELECT job_id, status, auto_created FROM job_applications WHERE profile_id = :pid ORDER BY job_id'
                    ), {'pid': profile_id}).all(), [
                        (job_ids[1], 'new', False),
                        (job_ids[2], 'open', False),
                        (job_ids[3], 'waiting_for_reply', False),
                    ])
            finally:
                engine.dispose()

    def test_application_documents_survive_entrypoint_rollback_and_reupgrade(self):
        from docker import entrypoint

        old_head = '0043_drop_ai_assessment'
        with patch.dict(os.environ, {'DATABASE_URL': self.target}):
            command.upgrade(self.config, old_head)
        engine = create_engine(self.target)
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO user_profiles (id, name, preferences, created_at, updated_at) "
                "VALUES (1, 'Fictional applicant', '{}', now(), now())"))
            connection.execute(text(
                "INSERT INTO companies (id, source, source_id, name, career_status, discovery_attempt_count) "
                "VALUES (1, 'manual', 'fixture', 'Fictional employer', 'not_checked', 0)"))
            connection.execute(text(
                "INSERT INTO job_feeds (id, company_id, provider, feed_url, status, enabled, attempt_count) "
                "VALUES (1, 1, 'rss', 'https://example.test/feed', 'ok', TRUE, 0)"))
            connection.execute(text(
                "INSERT INTO jobs (id, feed_id, external_id, title, url, is_remote, raw_metadata, enrichment, "
                "first_seen_at, last_seen_at, is_active, missing_complete_scans) "
                "VALUES (2, 1, 'job', 'Engineer', 'https://example.test/job', FALSE, '{}', '{}', now(), now(), TRUE, 0)"))

        # The disposable database already has the role. The entrypoint's role
        # bootstrap targets the production database name.
        executable_dir = str(Path(sys.executable).parent)
        with patch.object(entrypoint, '_application_database_url', return_value=self.target), \
                patch.object(entrypoint, '_application_database_password', return_value='unused'), \
                patch.object(entrypoint, '_ensure_application_role'), \
                patch.dict(os.environ, {'PATH': executable_dir + os.pathsep + os.environ['PATH']}):
            entrypoint._migrate(self.target)
            with engine.begin() as connection:
                connection.execute(text('GRANT USAGE ON SCHEMA public TO hiring_app'))
                connection.execute(text('SET LOCAL ROLE hiring_app'))
                connection.execute(text(
                    "INSERT INTO application_documents "
                    "(profile_id, job_id, kind, version, language, body, origin, created_at) "
                    "VALUES (1, 2, 'letter', 1, 'en', :body, 'user', now())"
                ), {'body': '{"text": "Fictional manual edit"}'})
                connection.execute(text(
                    "INSERT INTO application_document_renders "
                    "(profile_id, job_id, kind, version, template, pdf, pages, created_at) "
                    "VALUES (1, 2, 'letter', 1, 'fixture', :pdf, 1, now())"
                ), {'pdf': b'fictional PDF'})
                connection.execute(text(
                    "INSERT INTO application_document_preparation_tasks "
                    "(profile_id, job_id, record, state, updated_at) "
                    "VALUES (1, 2, :record, 'running', now())"
                ), {'record': '{"kind": "prepare"}'})

            entrypoint._migrate(self.target, rollback_application_documents=True)
            # Give the previous image's startup migration only the revisions
            # it shipped, so this fails if the rollback leaves an unknown head.
            old_config = Config()
            migrations = Path('hiring_scraper/app/migrations').resolve()
            old_config.set_main_option('script_location', str(migrations))
            with TemporaryDirectory() as folder, patch.dict(os.environ, {'DATABASE_URL': self.target}):
                for file in (migrations / 'versions').glob('*.py'):
                    # The previous image shipped none of 0044 (application documents v2),
                    # 0045 (local job analysis), or 0046 (precomputed job location scopes).
                    if not file.name.startswith(('0044_', '0045_', '0046_')):
                        (Path(folder) / file.name).symlink_to(file)
                old_config.set_main_option('version_locations', folder)
                command.upgrade(old_config, 'head')
            with engine.connect() as connection:
                self.assertEqual(connection.scalar(text('SELECT version_num FROM alembic_version')), old_head)
                connection.execute(text('SET LOCAL ROLE hiring_app'))
                self.assertEqual(connection.scalar(text(
                    "SELECT count(*) FROM application_preparation_tasks WHERE state = 'running'")), 0)

            entrypoint._migrate(self.target)
            with engine.connect() as connection:
                connection.execute(text('SET LOCAL ROLE hiring_app'))
                self.assertEqual(connection.scalar(text(
                    "SELECT body->>'text' FROM application_documents")), 'Fictional manual edit')
                self.assertEqual(connection.scalar(text(
                    'SELECT pdf FROM application_document_renders')), b'fictional PDF')
                self.assertEqual(connection.scalar(text(
                    "SELECT record->>'kind' FROM application_document_preparation_tasks")), 'prepare')

    def test_0046_retries_child_ddl_without_blocking_job_writes(self):
        self.assert_0046_child_ddl_does_not_block_job_writes('0045_local_job_analysis')

    def test_upgrade_through_0045_and_0046_does_not_hold_job_locks(self):
        # 0045 locks jobs; with one transaction per migration that lock is released
        # before 0046 waits for job_locations.
        self.assert_0046_child_ddl_does_not_block_job_writes('0044_application_documents_v2')

    def assert_0046_child_ddl_does_not_block_job_writes(self, start):
        with patch.dict(os.environ, {'DATABASE_URL': self.target}):
            command.upgrade(self.config, start)
            engine = create_engine(self.target)
            self.addCleanup(engine.dispose)
            with engine.begin() as connection:
                connection.execute(text("""
                    WITH company AS (
                        INSERT INTO companies (source, source_id, name, career_status, discovery_attempt_count)
                        VALUES ('manual', 'ddl', 'Employer', 'not_checked', 0) RETURNING id
                    ), feed AS (
                        INSERT INTO job_feeds (company_id, provider, feed_url, status, enabled, attempt_count)
                        SELECT id, 'rss', 'https://example.test/feed', 'ok', TRUE, 0 FROM company RETURNING id
                    ), job AS (
                        INSERT INTO jobs (feed_id, external_id, title, url, is_remote, raw_metadata, enrichment,
                                          first_seen_at, last_seen_at, is_active, missing_complete_scans)
                        SELECT id, '1', 'Engineer', 'https://example.test/job', FALSE, '{}', '{}',
                               now(), now(), TRUE, 0 FROM feed RETURNING id
                    )
                    INSERT INTO job_locations (job_id, label) SELECT id, 'Paris' FROM job
                """))
            retrying = threading.Event()
            attempts = 0

            def observe_child_ddl(_connection, _cursor, statement, *_args):
                nonlocal attempts
                if statement.startswith(('LOCK TABLE job_locations',
                                         'DROP TRIGGER IF EXISTS job_locations_invalidate_location_scope')):
                    attempts += 1
                    if attempts == 2:
                        retrying.set()

            event.listen(engine.__class__, 'before_cursor_execute', observe_child_ddl)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with engine.begin() as writer, engine.connect() as observer:
                        writer.execute(text("UPDATE job_locations SET label = 'Lyon'"))
                        pid = writer.scalar(text('SELECT pg_backend_pid()'))
                        migration = pool.submit(command.upgrade, self.config, 'head')
                        pg.wait_for_blocker(observer, pid)
                        with engine.begin() as job_writer:
                            job_writer.execute(text("SET LOCAL lock_timeout = '1s'"))
                            job_writer.execute(text("UPDATE jobs SET title = 'Senior Engineer'"))
                        self.assertTrue(retrying.wait(5), 'migration did not retry while the child writer was active')
                    migration.result(timeout=15)
            finally:
                event.remove(engine.__class__, 'before_cursor_execute', observe_child_ddl)

            with engine.connect() as connection:
                self.assertEqual(connection.scalar(text('SELECT version_num FROM alembic_version')),
                                 '0046_job_location_scope')
                self.assertEqual(connection.scalar(text('SELECT title FROM jobs')), 'Senior Engineer')
                self.assertEqual(connection.scalar(text('SELECT label FROM job_locations')), 'Lyon')

    def test_0046_job_location_scope_up_down_up_and_worker_backfill(self):
        from hiring_scraper.app.job_location_scope import LOCATION_SCOPE_VERSION, backfill_job_location_scopes_once

        expected_columns = {'germany_candidate', 'location_states', 'location_scope_version'}
        expected_indexes = {
            'ix_jobs_germany_candidate',
            'ix_jobs_location_states',
            'ix_jobs_location_scope_version',
            'ix_jobs_remote_candidate_active',
        }
        with patch.dict(os.environ, {'DATABASE_URL': self.target}):
            command.upgrade(self.config, '0045_local_job_analysis')
            engine = create_engine(self.target)
            self.addCleanup(engine.dispose)
            with engine.begin() as connection:
                connection.execute(text(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hiring_app') "
                    "THEN CREATE ROLE hiring_app; END IF; END $$"))
                connection.execute(text('GRANT USAGE ON SCHEMA public TO hiring_app'))
                company_id = connection.execute(text(
                    "INSERT INTO companies (source, source_id, name, career_status, discovery_attempt_count) "
                    "VALUES ('manual', 'c-scope', 'Scope GmbH', 'not_checked', 0) RETURNING id"
                )).scalar_one()
                feed_id = connection.execute(text(
                    "INSERT INTO job_feeds (company_id, provider, feed_url, status, enabled, attempt_count) "
                    "VALUES (:company_id, 'rss', 'https://example.com/scope.xml', 'ok', TRUE, 0) RETURNING id"
                ), {'company_id': company_id}).scalar_one()
                seed_jobs = [
                    ('munich', 'München', None),
                    ('berlin-cc', 'Remote', ('Berlin Office', 'DE', None, None)),
                    ('bayern-coords', 'Werk', ('South Site', None, 48.1351, 11.5820)),
                    ('paris', 'Paris, France', ('Paris', 'FR', 48.8566, 2.3522)),
                ]
                for ext, location_text, loc in seed_jobs:
                    job_id = connection.execute(text(
                        "INSERT INTO jobs (feed_id, external_id, title, url, location_text, is_remote, "
                        "raw_metadata, enrichment, first_seen_at, last_seen_at, is_active, missing_complete_scans) "
                        "VALUES (:feed_id, :ext, :title, :url, :loc, FALSE, '{}', '{}', now(), now(), TRUE, 0) "
                        "RETURNING id"
                    ), {
                        'feed_id': feed_id,
                        'ext': ext,
                        'title': f'Role {ext}',
                        'url': f'https://example.com/jobs/{ext}',
                        'loc': location_text,
                    }).scalar_one()
                    if loc is not None:
                        label, country_code, lat, lon = loc
                        connection.execute(text(
                            "INSERT INTO job_locations (job_id, label, country_code, latitude, longitude) "
                            "VALUES (:job_id, :label, :country_code, :lat, :lon)"
                        ), {
                            'job_id': job_id,
                            'label': label,
                            'country_code': country_code,
                            'lat': lat,
                            'lon': lon,
                        })

            def assert_upgraded():
                with engine.begin() as connection:
                    self.assertEqual(
                        connection.scalar(text('SELECT version_num FROM alembic_version')),
                        '0046_job_location_scope',
                    )
                    cols = set(connection.scalars(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'jobs'"
                    )).all())
                    self.assertTrue(expected_columns.issubset(cols))
                    indexes = dict(connection.execute(text(
                        "SELECT indexrelid::regclass::text, indisvalid FROM pg_index "
                        "WHERE indrelid = 'jobs'::regclass"
                    )).all())
                    self.assertTrue(all(indexes.get(name) for name in expected_indexes))
                    connection.execute(text('SET LOCAL ROLE hiring_app'))
                    self.assertEqual(connection.execute(text(
                        "SELECT DISTINCT germany_candidate, location_states, location_scope_version FROM jobs"
                    )).all(), [(None, None, None)])
                    connection.execute(text('UPDATE jobs SET location_text = location_text'))
                    job_id = connection.scalar(text(
                        "UPDATE job_locations SET label = label || ' Annex' WHERE country_code = 'DE' RETURNING job_id"
                    ))
                    self.assertEqual(connection.scalar(text('SELECT job_id FROM job_location_scope_dirty')), job_id)

                with engine.connect() as connection:
                    with connection.begin():
                        connection.execute(text('SET LOCAL ROLE hiring_app'))
                        self.assertEqual(backfill_job_location_scopes_once(
                            session_factory=sessionmaker(bind=connection),
                        ), 4)
                with engine.begin() as connection:
                    connection.execute(text('SET LOCAL ROLE hiring_app'))
                    rows = {
                        row[0]: (row[1], sorted(row[2] or []), row[3])
                        for row in connection.execute(text(
                            "SELECT external_id, germany_candidate, location_states, location_scope_version "
                            "FROM jobs"
                        )).all()
                    }
                    self.assertEqual(rows['munich'], (True, ['Bayern'], LOCATION_SCOPE_VERSION))
                    self.assertEqual(rows['berlin-cc'], (True, ['Berlin'], LOCATION_SCOPE_VERSION))
                    self.assertEqual(rows['bayern-coords'][0], True)
                    self.assertIn('Bayern', rows['bayern-coords'][1])
                    self.assertEqual(rows['bayern-coords'][2], LOCATION_SCOPE_VERSION)
                    self.assertEqual(rows['paris'], (False, [], LOCATION_SCOPE_VERSION))

            # Interrupt after the nullable columns and triggers commit. Check
            # the migration connection no longer holds a lock blocking job writes.
            class Interrupted(Exception):
                pass

            def interrupt_index_build(_connection, _cursor, statement, *_args):
                if not statement.startswith('CREATE INDEX CONCURRENTLY'):
                    return
                with engine.begin() as writer:
                    writer.execute(text("SET LOCAL lock_timeout = '1s'"))
                    writer.execute(text("UPDATE jobs SET title = title"))
                raise Interrupted

            event.listen(engine.__class__, 'before_cursor_execute', interrupt_index_build)
            try:
                with self.assertRaises(Interrupted):
                    command.upgrade(self.config, 'head')
            finally:
                event.remove(engine.__class__, 'before_cursor_execute', interrupt_index_build)

            # A failed concurrent build leaves its name registered as INVALID.
            # All four jobs share is_active, so a unique index cannot be built.
            with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                with self.assertRaises(IntegrityError):
                    connection.execute(text(
                        "CREATE UNIQUE INDEX CONCURRENTLY ix_jobs_germany_candidate ON jobs (is_active)"
                    ))
                self.assertFalse(connection.scalar(text(
                    "SELECT indisvalid FROM pg_index WHERE indexrelid = 'ix_jobs_germany_candidate'::regclass"
                )))
                connection.execute(text(
                    "CREATE INDEX CONCURRENTLY ix_jobs_location_scope_version ON jobs (location_scope_version, id)"
                ))

            command.upgrade(self.config, 'head')
            assert_upgraded()

            command.downgrade(self.config, '0045_local_job_analysis')
            with engine.connect() as connection:
                self.assertEqual(
                    connection.scalar(text('SELECT version_num FROM alembic_version')),
                    '0045_local_job_analysis',
                )
                cols = set(connection.scalars(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'jobs'"
                )).all())
                self.assertTrue(expected_columns.isdisjoint(cols))
                indexes = set(connection.scalars(text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = 'jobs'"
                )).all())
                self.assertTrue(expected_indexes.isdisjoint(indexes))

            command.upgrade(self.config, 'head')
            assert_upgraded()


if __name__ == '__main__':
    unittest.main()
