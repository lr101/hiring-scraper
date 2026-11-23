"""Bring the database in DATABASE_URL to the production schema.

Creates the ``hiring_app`` role migrations grant to if it is missing, runs
``alembic upgrade head``, and applies the rollback-safe schema step the container
entrypoint applies afterwards, so local, preview and test databases match a
deployment exactly. Safe to repeat.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ensure_app_role(database_url: str) -> None:
    """Migrations grant to ``hiring_app``; Compose creates it, a fresh dev server may not."""
    from sqlalchemy import create_engine, text
    engine = create_engine(database_url, isolation_level='AUTOCOMMIT')
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = 'hiring_app'")) is None:
                connection.execute(text('CREATE ROLE hiring_app NOLOGIN'))
    finally:
        engine.dispose()


def migrate(database_url: str) -> None:
    _ensure_app_role(database_url)
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT,
                   env=dict(os.environ, DATABASE_URL=database_url), check=True, stdout=subprocess.DEVNULL)
    spec = importlib.util.spec_from_file_location('radius_entrypoint', ROOT / 'docker' / 'entrypoint.py')
    entrypoint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrypoint)
    entrypoint._ensure_profile_vote_weight(database_url)


if __name__ == '__main__':
    url = os.environ.get('DATABASE_URL', '')
    if not url.startswith(('postgresql:', 'postgresql+')):
        sys.exit('Set DATABASE_URL to the PostgreSQL database to migrate.')
    migrate(url)
    print('Database schema is current.')
