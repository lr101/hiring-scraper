"""Database connection and session helpers."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Generator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./hiring.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite:")
_engine_options: dict[str, object] = {"pool_pre_ping": True}
if IS_SQLITE:
    # The API and background workers share SQLite in local previews. Let
    # concurrent writers wait for the active transaction instead of failing
    # after sqlite3's short default timeout.
    _engine_options["connect_args"] = {"check_same_thread": False, "timeout": 30}

engine = create_engine(DATABASE_URL, **_engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_sqlite_schema(target_engine=engine) -> None:
    """Keep local databases created with create_all compatible with the PoC.

    PostgreSQL deployments use Alembic instead. This additive SQLite upgrade
    preserves the dated source data and can safely be repeated at startup.
    """
    from hiring_scraper.app.models import Base
    if target_engine.dialect.name != 'sqlite':
        return
    with target_engine.begin() as connection:
        # Older development databases may contain duplicate active Germany
        # inventories from before the coalescing guard was added.
        tables = set(inspect(connection).get_table_names())
        if 'discovery_jobs' in tables:
            job_columns = {column['name'] for column in inspect(connection).get_columns('discovery_jobs')}
            if {'search_scope', 'stage', 'status'} <= job_columns:
                active_ids = connection.exec_driver_sql(
                    "SELECT id FROM discovery_jobs WHERE search_scope='country' "
                    "AND stage IN ('company_homepage_discovery','career_page_discovery') "
                    "AND status IN ('scheduled','queued','running') ORDER BY id"
                ).scalars().all()
                for duplicate_id in active_ids[1:]:
                    connection.exec_driver_sql(
                        "UPDATE discovery_jobs SET status='cancelled', stage='complete', "
                        "finished_at=CURRENT_TIMESTAMP, error='Superseded by the active Germany inventory.' "
                        "WHERE id=?", (duplicate_id,))
        Base.metadata.create_all(connection)
        if 'application_momentum_settings' in inspect(connection).get_table_names():
            connection.exec_driver_sql("DROP TABLE application_momentum_settings")

        momentum_events_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='application_momentum_events'").scalar() or ""
        if "interview" not in momentum_events_sql.lower() or "accepted" not in momentum_events_sql.lower():
            connection.exec_driver_sql("""
                CREATE TABLE application_momentum_events_next (
                    id INTEGER NOT NULL PRIMARY KEY,
                    profile_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
                    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    event_type VARCHAR(32) NOT NULL,
                    occurred_at DATETIME NOT NULL,
                    is_legacy BOOLEAN DEFAULT 0 NOT NULL,
                    CONSTRAINT ck_application_momentum_event_type
                        CHECK (event_type IN ('applied', 'interview', 'accepted'))
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO application_momentum_events_next
                    (id, profile_id, job_id, event_type, occurred_at, is_legacy)
                SELECT id, profile_id, job_id, event_type, occurred_at, is_legacy
                  FROM application_momentum_events
            """)
            connection.exec_driver_sql("DROP TABLE application_momentum_events")
            connection.exec_driver_sql(
                "ALTER TABLE application_momentum_events_next RENAME TO application_momentum_events")
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_application_momentum_events_type_date "
                "ON application_momentum_events (event_type, occurred_at)")
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_application_momentum_events_job "
                "ON application_momentum_events (job_id)")
        snapshot_columns = {column['name'] for column in inspect(connection).get_columns('job_search_snapshots')}
        if 'criteria_hash' not in snapshot_columns:
            connection.exec_driver_sql(
                "ALTER TABLE job_search_snapshots ADD COLUMN criteria_hash VARCHAR(64) NOT NULL DEFAULT ''")
        if 'is_prewarmed' not in snapshot_columns:
            connection.exec_driver_sql(
                "ALTER TABLE job_search_snapshots ADD COLUMN is_prewarmed BOOLEAN NOT NULL DEFAULT 0")
        refresh_columns = {column['name'] for column in inspect(connection).get_columns('job_search_snapshot_refreshes')}
        if 'prewarm' not in refresh_columns:
            connection.exec_driver_sql(
                "ALTER TABLE job_search_snapshot_refreshes ADD COLUMN prewarm BOOLEAN NOT NULL DEFAULT 0")
        for snapshot_key, payload in connection.exec_driver_sql(
                "SELECT key, payload FROM job_search_snapshots").all():
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            criteria = payload.get('criteria') if isinstance(payload, dict) else None
            if not isinstance(criteria, dict):
                continue
            stable = {name: value for name, value in criteria.items() if name != 'freshness_bucket'}
            signature = hashlib.sha256(json.dumps(
                stable, sort_keys=True, ensure_ascii=False, separators=(',', ':')
            ).encode('utf-8')).hexdigest()
            connection.exec_driver_sql(
                "UPDATE job_search_snapshots SET criteria_hash=? WHERE key=? AND criteria_hash=''",
                (signature, snapshot_key))
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_job_search_snapshots_criteria_created "
            "ON job_search_snapshots (criteria_hash, created_at)")
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS companies_semantic_refresh_insert
            AFTER INSERT ON companies BEGIN
                DELETE FROM semantic_search_documents
                 WHERE entity_type = 'company' AND entity_id = NEW.id;
                INSERT INTO semantic_search_refreshes(entity_type, entity_id, queued_at)
                VALUES ('company', NEW.id, CURRENT_TIMESTAMP)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                    queued_at = excluded.queued_at, lease_token = NULL, lease_until = NULL;
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS companies_semantic_refresh_update
            AFTER UPDATE OF name, domain, category, state, location_label ON companies BEGIN
                DELETE FROM semantic_search_documents
                 WHERE entity_type = 'company' AND entity_id = NEW.id;
                INSERT INTO semantic_search_refreshes(entity_type, entity_id, queued_at)
                VALUES ('company', NEW.id, CURRENT_TIMESTAMP)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                    queued_at = excluded.queued_at, lease_token = NULL, lease_until = NULL;
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS jobs_semantic_refresh_insert
            AFTER INSERT ON jobs BEGIN
                DELETE FROM semantic_search_documents
                 WHERE entity_type = 'job' AND entity_id = NEW.id;
                INSERT INTO semantic_search_refreshes(entity_type, entity_id, queued_at)
                VALUES ('job', NEW.id, CURRENT_TIMESTAMP)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                    queued_at = excluded.queued_at, lease_token = NULL, lease_until = NULL;
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS jobs_semantic_refresh_update
            AFTER UPDATE OF title, description, location_text, is_remote, work_arrangement,
                employment_type, schedule, department, seniority ON jobs BEGIN
                DELETE FROM semantic_search_documents
                 WHERE entity_type = 'job' AND entity_id = NEW.id;
                INSERT INTO semantic_search_refreshes(entity_type, entity_id, queued_at)
                VALUES ('job', NEW.id, CURRENT_TIMESTAMP)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                    queued_at = excluded.queued_at, lease_token = NULL, lease_until = NULL;
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS companies_semantic_purge
            AFTER DELETE ON companies BEGIN
                DELETE FROM semantic_search_documents
                 WHERE entity_type = 'company' AND entity_id = OLD.id;
                DELETE FROM semantic_search_refreshes
                 WHERE entity_type = 'company' AND entity_id = OLD.id;
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS jobs_semantic_purge
            AFTER DELETE ON jobs BEGIN
                DELETE FROM semantic_search_documents
                 WHERE entity_type = 'job' AND entity_id = OLD.id;
                DELETE FROM semantic_search_refreshes
                 WHERE entity_type = 'job' AND entity_id = OLD.id;
            END
        """)
        if 'job_applications' in set(inspect(connection).get_table_names()):
            application_columns = {column['name'] for column in inspect(connection).get_columns('job_applications')}
            if 'previous_status' not in application_columns:
                connection.exec_driver_sql("ALTER TABLE job_applications ADD COLUMN previous_status VARCHAR(24)")
        profile_columns = {column['name'] for column in inspect(connection).get_columns('user_profiles')}
        if 'match_index_version' not in profile_columns:
            connection.exec_driver_sql("ALTER TABLE user_profiles ADD COLUMN match_index_version VARCHAR(40)")
        application_columns = {column['name'] for column in inspect(connection).get_columns('job_applications')}
        if 'auto_created' not in application_columns:
            connection.exec_driver_sql("ALTER TABLE job_applications ADD COLUMN auto_created BOOLEAN NOT NULL DEFAULT 0")
        connection.exec_driver_sql("""
            INSERT INTO application_momentum_events
                (profile_id, job_id, event_type, occurred_at, is_legacy)
            SELECT application.profile_id, application.job_id, 'applied', application.updated_at, 1
              FROM job_applications AS application
             WHERE (
                    application.status IN ('waiting_for_reply', 'interview', 'accepted')
                    OR (application.status = 'rejected'
                        AND application.previous_status IN ('waiting_for_reply', 'interview', 'accepted'))
                   )
               AND NOT EXISTS (
                    SELECT 1
                      FROM application_momentum_events AS existing
                     WHERE existing.profile_id = application.profile_id
                       AND existing.job_id = application.job_id
                       AND existing.event_type = 'applied'
                       AND existing.is_legacy = 1
               )
        """)
        for event_type, legacy_filter in (
                ('interview', "application.status = 'interview' OR application.previous_status = 'interview'"),
                ('accepted', "application.status = 'accepted' OR application.previous_status = 'accepted'")):
            connection.exec_driver_sql(f"""
                INSERT INTO application_momentum_events
                    (profile_id, job_id, event_type, occurred_at, is_legacy)
                SELECT application.profile_id, application.job_id, '{event_type}', application.updated_at, 1
                  FROM job_applications AS application
                 WHERE ({legacy_filter})
                   AND NOT EXISTS (
                        SELECT 1
                          FROM application_momentum_events AS existing
                         WHERE existing.profile_id = application.profile_id
                           AND existing.job_id = application.job_id
                           AND existing.event_type = '{event_type}'
                           AND existing.is_legacy = 1
                   )
            """)
        feed_columns = {column['name'] for column in inspect(connection).get_columns('job_feeds')}
        if 'enabled' not in feed_columns:
            connection.exec_driver_sql("ALTER TABLE job_feeds ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 1")
        if 'source_revision' not in feed_columns:
            connection.exec_driver_sql("ALTER TABLE job_feeds ADD COLUMN source_revision INTEGER NOT NULL DEFAULT 1")
        if 'source_config' not in feed_columns:
            connection.exec_driver_sql("ALTER TABLE job_feeds ADD COLUMN source_config JSON NOT NULL DEFAULT '{}'")
        if 'pending_search_scopes' not in feed_columns:
            connection.exec_driver_sql("ALTER TABLE job_feeds ADD COLUMN pending_search_scopes JSON NOT NULL DEFAULT '[]'")
        scan_columns = {column['name'] for column in inspect(connection).get_columns('scan_runs')}
        if 'search_scope' not in scan_columns:
            connection.exec_driver_sql("ALTER TABLE scan_runs ADD COLUMN search_scope JSON")
        columns = {column['name'] for column in inspect(connection).get_columns('jobs')}
        if 'enrichment' not in columns:
            connection.exec_driver_sql("ALTER TABLE jobs ADD COLUMN enrichment JSON NOT NULL DEFAULT '{}'")
        if 'employer_company_id' not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN employer_company_id INTEGER "
                "REFERENCES companies(id) ON DELETE SET NULL")
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_jobs_employer_company_id ON jobs (employer_company_id)")
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_jobs_active_id ON jobs (id) WHERE is_active = 1")
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_profile_job_matches_job_profile ON profile_job_matches (job_id, profile_id)")
        if connection.exec_driver_sql("SELECT id FROM job_search_state LIMIT 1").first() is None:
            connection.exec_driver_sql("INSERT INTO job_search_state DEFAULT VALUES")

        for table in ('configured_locations', 'discovery_jobs'):
            columns = {column['name'] for column in inspect(connection).get_columns(table)}
            if 'search_scope' not in columns:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN search_scope VARCHAR(16) NOT NULL DEFAULT 'radius'")
        columns = {column['name'] for column in inspect(connection).get_columns('companies')}
        if 'state' not in columns:
            connection.exec_driver_sql("ALTER TABLE companies ADD COLUMN state VARCHAR(120)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_companies_state ON companies (state)")
        additions = {
            'latitude': 'FLOAT', 'longitude': 'FLOAT',
            'country_code': 'VARCHAR(2)', 'employee_count': 'INTEGER',
            'size_evidence': "JSON NOT NULL DEFAULT '{}'", 'crawl_priority': 'INTEGER NOT NULL DEFAULT 0',
            'crawl_interval_days': 'INTEGER DEFAULT 7', 'crawl_reason': 'TEXT',
            'crawl_paused': 'BOOLEAN NOT NULL DEFAULT 0', 'irrelevant_checks': 'INTEGER NOT NULL DEFAULT 0',
            'career_url_manual': 'BOOLEAN NOT NULL DEFAULT 0',
            'ranking_updated_at': 'DATETIME', 'homepage_source_tags': 'JSON',
            'homepage_resolution_attempts': 'INTEGER NOT NULL DEFAULT 0',
            'homepage_resolution_next_at': 'DATETIME', 'homepage_resolution_lease_until': 'DATETIME',
            'homepage_resolution_token': 'VARCHAR(36)',
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f'ALTER TABLE companies ADD COLUMN {name} {declaration}')
        for name in ('country_code', 'crawl_priority'):
            connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS ix_companies_{name} ON companies ({name})')
        for name in ('homepage_resolution_next_at', 'homepage_resolution_lease_until'):
            connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS ix_companies_{name} ON companies ({name})')
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_companies_latitude_longitude ON companies (latitude, longitude)")
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_job_locations_latitude_longitude ON job_locations (latitude, longitude)")
        job_columns = {column['name'] for column in inspect(connection).get_columns('discovery_jobs')}
        if 'map_cursor' not in job_columns:
            connection.exec_driver_sql("ALTER TABLE discovery_jobs ADD COLUMN map_cursor JSON NOT NULL DEFAULT '{}'")
        job_columns = {column['name'] for column in inspect(connection).get_columns('discovery_jobs')}
        if {'search_scope', 'stage', 'status'} <= job_columns:
            connection.exec_driver_sql("DROP INDEX IF EXISTS uq_active_germany_inventory")
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_germany_inventory ON discovery_jobs(search_scope) "
                "WHERE search_scope='country' AND stage IN ('company_homepage_discovery','career_page_discovery') "
                "AND status IN ('scheduled','queued','running')"
            )


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
