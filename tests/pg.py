"""PostgreSQL test database shared by the unit suite.

Every database test runs against PostgreSQL with the real migrated schema, so
tests exercise the same triggers, generated columns and indexes as production.
``TEST_POSTGRES_URL`` must name a disposable database: it is migrated once per
process and every table is truncated before each test that asks for an engine.

Run the suite with ``scripts/dev/postgres-sandbox.sh`` (throwaway local cluster),
``scripts/ci/postgres-local.sh`` (existing local test server) or the container CI.
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

URL = os.environ.get('TEST_POSTGRES_URL', '')
# Tests fail when a write path forgets to declare its changes (see app/source_changes.py).
os.environ.setdefault('RADIUS_STRICT_SOURCE_CHANGES', '1')
_lock = threading.Lock()
_tables: list[str] | None = None
# Rows that migrations seed and production code expects to exist.
_KEEP = {'alembic_version', 'spatial_ref_sys'}


def url() -> str:
    if not URL or not URL.startswith(('postgresql:', 'postgresql+')):
        raise RuntimeError(
            'Database tests need a disposable PostgreSQL database in TEST_POSTGRES_URL '
            '(and DATABASE_URL set to the same value). Run them with '
            'scripts/dev/postgres-sandbox.sh or scripts/ci/postgres-local.sh.')
    if os.environ.get('DATABASE_URL') != URL:
        raise RuntimeError('Set DATABASE_URL to the same disposable database as TEST_POSTGRES_URL.')
    # The harness terminates sessions and truncates every table: never accept a real database.
    if not re.fullmatch(r'(hiring|radius)_test\w*', make_url(URL).database or ''):
        raise RuntimeError('TEST_POSTGRES_URL must name a disposable hiring_test*/radius_test* database.')
    return URL


def _prepare() -> list[str]:
    """Migrate the test database to head once per process."""
    global _tables
    with _lock:
        if _tables is not None:
            return _tables
        from alembic import command
        from alembic.config import Config

        admin = create_engine(url(), isolation_level='AUTOCOMMIT')
        try:
            with admin.connect() as connection:
                connection.execute(text(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hiring_app') "
                    "THEN CREATE ROLE hiring_app NOLOGIN; END IF; END $$"))
            config = Config()
            config.set_main_option('script_location', 'hiring_scraper/app/migrations')
            config.set_main_option('sqlalchemy.url', url())
            command.upgrade(config, 'head')
            _deployment_schema_steps(url())
            with admin.connect() as connection:
                _tables = [name for name in connection.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")).scalars()
                    if name not in _KEEP]
        finally:
            admin.dispose()
        return _tables


def _deployment_schema_steps(database_url: str) -> None:
    """Apply the rollback-safe schema steps the container entrypoint runs after Alembic."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'docker' / 'entrypoint.py'
    spec = importlib.util.spec_from_file_location('radius_entrypoint_for_tests', path)
    entrypoint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrypoint)
    entrypoint._ensure_profile_vote_weight(database_url)


def reset() -> None:
    """Empty every application table (identity counters restart at 1)."""
    tables = _prepare()
    admin = create_engine(url(), isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as connection:
            # A previous test may have leaked a session mid-transaction (for
            # example after an expected failure). End it so it cannot hold locks.
            connection.execute(text(
                'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                'WHERE datname = current_database() AND pid <> pg_backend_pid()'))
            connection.execute(text('SET lock_timeout = 10000'))
            connection.execute(text('TRUNCATE ' + ', '.join(f'"{name}"' for name in tables)
                                    + ' RESTART IDENTITY CASCADE'))
    finally:
        admin.dispose()


# A test that waits on its own open transaction should fail, not hang the suite.
_DEFAULT_OPTIONS = '-c lock_timeout=20000 -c statement_timeout=120000'


def _with_default_timeouts(options: dict) -> dict:
    connect_args = dict(options.pop('connect_args', {}))
    connect_args['options'] = f"{_DEFAULT_OPTIONS} {connect_args.get('options', '')}".strip()
    return {**options, 'connect_args': connect_args}


def engine(test, **options) -> Engine:
    """An engine on a freshly emptied, fully migrated database; disposed after ``test``."""
    reset()
    created = create_engine(url(), **_with_default_timeouts(options))
    test.addCleanup(created.dispose)
    return created


def isolated_engine(test, **options) -> Engine:
    """An engine on a new, empty private schema, dropped after ``test``.

    For tests that run DDL or individual migrations (upgrade/downgrade) and must
    not alter the shared migrated schema. Only the private schema is on the
    search path: with ``public`` visible, SQLAlchemy would treat public tables as
    existing (skipping them in create_all) and migrations could drop them. Use
    ``create_isolated_schema`` to create model tables there.
    """
    from uuid import uuid4

    _prepare()
    schema = 'isolated_' + uuid4().hex
    admin = create_engine(url(), isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    connect_args = dict(options.pop('connect_args', {}))
    connect_args['options'] = f'{_DEFAULT_OPTIONS} -c search_path={schema}'
    created = create_engine(url(), connect_args=connect_args, **options)

    def cleanup():
        created.dispose()
        with admin.connect() as connection:
            # A failed test may leave a session open in this schema; end it first.
            connection.execute(text(
                'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                'WHERE datname = current_database() AND pid <> pg_backend_pid()'))
            connection.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        admin.dispose()

    test.addCleanup(cleanup)
    return created


def create_isolated_schema(bound: Engine, *, exclude=()) -> None:
    """Create model tables in an isolated schema (pgvector tables are skipped)."""
    from hiring_scraper.app.models import Base

    skip = {'semantic_search_documents', *exclude}
    Base.metadata.create_all(bound, tables=[table for table in Base.metadata.sorted_tables
                                            if table.name not in skip])


def session(test, **options) -> Session:
    """A Session on a fresh database, closed and disposed after ``test``."""
    bound = engine(test, **options)
    opened = Session(bound)
    test.addCleanup(opened.close)
    return opened


def factory(test, **options) -> sessionmaker:
    return sessionmaker(engine(test, **options), autoflush=False, expire_on_commit=False)


def server_url() -> str:
    """The database URL for a subprocess (for example a browser-test API server)."""
    reset()
    return url()


@contextmanager
def patched_session_local(bound: Engine):
    """Point every imported ``SessionLocal``/``engine`` at ``bound`` (for worker code)."""
    import sys
    from contextlib import ExitStack
    from unittest.mock import patch

    factory_ = sessionmaker(bind=bound, autoflush=False, expire_on_commit=False)
    with ExitStack() as stack:
        for module in list(sys.modules.values()):
            name = getattr(module, '__name__', '') or ''
            if not name.startswith('hiring_scraper.'):
                continue
            if hasattr(module, 'SessionLocal'):
                stack.enter_context(patch.object(module, 'SessionLocal', factory_))
            if isinstance(getattr(module, 'engine', None), Engine):
                stack.enter_context(patch.object(module, 'engine', bound))
        yield factory_


def run_workers(bound: Engine, *, matching: bool = True, search: bool = True, max_rounds: int = 60) -> None:
    """Run queued background work to completion, as the production workers would.

    Reads never match or build search snapshots inside a request. Tests that
    assert on Journey or browse results call this after writing sources, then
    read again. Close or commit the test's own Session first so it holds no locks.
    """
    from unittest.mock import patch

    from hiring_scraper.app import job_search, search_snapshots

    # The worker debounces bursts of refresh requests; tests drain immediately.
    with patched_session_local(bound), patch.object(search_snapshots, '_SEARCH_SNAPSHOT_REFRESH_DELAY_SECONDS', 0):
        for _ in range(max_rounds):
            worked = False
            if matching:
                for step in (job_search.refresh_profile_match_indexes_once,
                             job_search.refresh_dirty_job_matches_once):
                    try:
                        worked = bool(step()) or worked
                    except job_search.ProfileIndexChanged:
                        worked = True
            if search:
                worked = bool(search_snapshots.process_search_snapshot_refresh_once()) or worked
            if not worked:
                return
    raise AssertionError('Background work did not settle')


def settled(bound: Engine, read, session: Session | None = None, *, matching: bool = True, search: bool = True):
    """Call ``read`` once (queueing work), run the workers, then return a fresh read."""
    read()
    if session is not None:
        session.commit()
    run_workers(bound, matching=matching, search=search)
    if session is not None:
        session.expire_all()
    return read()


def list_jobs(bound: Engine, *args, **kwargs):
    """``api.list_jobs`` after the search snapshot worker has built the page."""
    from hiring_scraper.app import api

    return settled(bound, lambda: api.list_jobs(*args, **kwargs), kwargs.get('session'), matching=False)


def list_applications(bound: Engine, *args, **kwargs):
    """``api.list_applications`` after the match worker has rebuilt the Journey."""
    from hiring_scraper.app import api

    return settled(bound, lambda: api.list_applications(*args, **kwargs), kwargs.get('session'), search=False)


def with_options(base: str, **settings) -> str:
    """Add libpq ``options`` (for example statement_timeout) to a database URL."""
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query))
    query['options'] = ' '.join(f'-c {key}={value}' for key, value in settings.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


__all__ = ['URL', 'list_applications', 'list_jobs', 'run_workers', 'settled', 'create_isolated_schema', 'engine', 'factory', 'isolated_engine', 'patched_session_local', 'reset', 'server_url', 'session',
           'url', 'with_options', 'make_url']
