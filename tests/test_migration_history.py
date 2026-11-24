"""Populated databases upgrade through historical data migrations to head."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

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


if __name__ == '__main__':
    unittest.main()
