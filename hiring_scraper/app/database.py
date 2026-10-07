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
        Base.metadata.create_all(connection)
        columns = {column['name'] for column in inspect(connection).get_columns('jobs')}
        if 'enrichment' not in columns:
            connection.exec_driver_sql("ALTER TABLE jobs ADD COLUMN enrichment JSON NOT NULL DEFAULT '{}'")


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
