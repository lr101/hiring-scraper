"""Database connection and session helpers."""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./hiring.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite:")
_engine_options: dict[str, object] = {"pool_pre_ping": True}
if IS_SQLITE:
    _engine_options["connect_args"] = {"check_same_thread": False}

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
                    "AND stage='company_homepage_discovery' "
                    "AND status IN ('scheduled','queued','running') ORDER BY id"
                ).scalars().all()
                for duplicate_id in active_ids[1:]:
                    connection.exec_driver_sql(
                        "UPDATE discovery_jobs SET status='cancelled', stage='complete', "
                        "finished_at=CURRENT_TIMESTAMP, error='Superseded by the active Germany inventory.' "
                        "WHERE id=?", (duplicate_id,))
        Base.metadata.create_all(connection)
        columns = {column['name'] for column in inspect(connection).get_columns('jobs')}
        if 'enrichment' not in columns:
            connection.exec_driver_sql("ALTER TABLE jobs ADD COLUMN enrichment JSON NOT NULL DEFAULT '{}'")

        for table in ('configured_locations', 'discovery_jobs'):
            columns = {column['name'] for column in inspect(connection).get_columns(table)}
            if 'search_scope' not in columns:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN search_scope VARCHAR(16) NOT NULL DEFAULT 'radius'")
        columns = {column['name'] for column in inspect(connection).get_columns('companies')}
        if 'state' not in columns:
            connection.exec_driver_sql("ALTER TABLE companies ADD COLUMN state VARCHAR(120)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_companies_state ON companies (state)")
        additions = {
            'country_code': 'VARCHAR(2)', 'employee_count': 'INTEGER',
            'size_evidence': "JSON NOT NULL DEFAULT '{}'", 'crawl_priority': 'INTEGER NOT NULL DEFAULT 0',
            'crawl_interval_days': 'INTEGER DEFAULT 7', 'crawl_reason': 'TEXT',
            'crawl_paused': 'BOOLEAN NOT NULL DEFAULT 0', 'irrelevant_checks': 'INTEGER NOT NULL DEFAULT 0',
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
        job_columns = {column['name'] for column in inspect(connection).get_columns('discovery_jobs')}
        if 'map_cursor' not in job_columns:
            connection.exec_driver_sql("ALTER TABLE discovery_jobs ADD COLUMN map_cursor JSON NOT NULL DEFAULT '{}'")
        job_columns = {column['name'] for column in inspect(connection).get_columns('discovery_jobs')}
        if {'search_scope', 'stage', 'status'} <= job_columns:
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_germany_inventory ON discovery_jobs(search_scope) "
                "WHERE search_scope='country' AND stage='company_homepage_discovery' "
                "AND status IN ('scheduled','queued','running')"
            )


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
