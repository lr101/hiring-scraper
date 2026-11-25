"""Create or drop a disposable, migrated PostgreSQL database for one preview slug.

    preview_database.py create NAME   # prints the database URL on stdout
    preview_database.py drop NAME

HIRING_PREVIEW_POSTGRES_URL names a role on a local test server that may create
and drop databases (for example postgresql+psycopg://postgres@127.0.0.1/postgres).
Names must start with ``radius_preview_`` so this tool cannot drop anything else.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def _admin_url() -> str:
    value = os.environ.get('HIRING_PREVIEW_POSTGRES_URL', '')
    if not value.startswith(('postgresql:', 'postgresql+')):
        sys.exit('Set HIRING_PREVIEW_POSTGRES_URL to a local PostgreSQL role that may create databases.')
    return value


def _checked(name: str) -> str:
    if not re.fullmatch(r'radius_preview_[a-z0-9_]{1,40}', name):
        sys.exit(f'Refusing unexpected preview database name: {name}')
    return name


def create(name: str) -> None:
    admin = create_engine(_admin_url(), isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{_checked(name)}" TEMPLATE template0'))
            connection.execute(text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hiring_app') "
                "THEN CREATE ROLE hiring_app NOLOGIN; END IF; END $$"))
    finally:
        admin.dispose()
    url = make_url(_admin_url()).set(database=name).render_as_string(hide_password=False)
    sys.path.insert(0, str(ROOT / 'scripts' / 'dev'))
    from migrate import migrate
    migrate(url)
    print(url)


def drop(name: str) -> None:
    admin = create_engine(_admin_url(), isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{_checked(name)}" WITH (FORCE)'))
    finally:
        admin.dispose()


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in {'create', 'drop'}:
        sys.exit(__doc__)
    (create if sys.argv[1] == 'create' else drop)(sys.argv[2])
