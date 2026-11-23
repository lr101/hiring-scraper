"""Database connection and session helpers."""
from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError, OperationalError, TimeoutError as DatabasePoolTimeout
from sqlalchemy.orm import Session, sessionmaker


# PostgreSQL (with PostGIS and pgvector) is the only supported database. The
# default uses libpq environment variables (PGHOST, PGUSER, ...) for database "hiring".
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg:///hiring")


def create_runtime_engine(database_url: str):
    """Budget each process's connections and bound PostgreSQL blocking waits."""
    if not database_url.startswith(('postgresql:', 'postgresql+')):
        raise RuntimeError('DATABASE_URL must be a PostgreSQL URL, for example postgresql+psycopg://user@host/hiring')
    postgres_settings = {
        'lock_timeout': max(1, int(os.getenv('DB_LOCK_TIMEOUT_MS', '5000'))),
        'statement_timeout': max(1, int(os.getenv('DB_STATEMENT_TIMEOUT_MS', '120000'))),
        'idle_in_transaction_session_timeout': max(1, int(os.getenv('DB_IDLE_TRANSACTION_TIMEOUT_MS', '120000'))),
    }
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=max(1, int(os.getenv('DB_POOL_SIZE') or os.getenv('HIRING_DB_POOL_SIZE', '4'))),
        max_overflow=max(0, int(os.getenv('DB_MAX_OVERFLOW') or os.getenv('HIRING_DB_MAX_OVERFLOW', '2'))),
        pool_timeout=max(.1, float(os.getenv('DB_POOL_TIMEOUT_SECONDS', '10'))),
        pool_recycle=1800,
        connect_args={
            'connect_timeout': max(1, int(os.getenv('DB_CONNECT_TIMEOUT_SECONDS', '5'))),
            'application_name': 'radius',
            'options': ' '.join(f'-c {name}={value}' for name, value in postgres_settings.items()),
        },
    )


def dialect_insert(_session: Session | None = None):
    """Return PostgreSQL's ``insert`` construct, which supports ``on_conflict_*``."""
    from sqlalchemy.dialects.postgresql import insert
    return insert


def is_transient_database_error(error: BaseException) -> bool:
    """Retry contention/connection failures, while surfacing schema/code errors."""
    if isinstance(error, DatabasePoolTimeout):
        return True
    if not isinstance(error, DBAPIError):
        return False
    if error.connection_invalidated:
        return True
    code = getattr(error.orig, 'sqlstate', None) or getattr(error.orig, 'pgcode', None)
    if code:
        return code.startswith('08') or code in {
            '40001', '40P01', '55P03', '57014', '57P01', '57P02', '57P03', '53300',
        }
    return isinstance(error, OperationalError) and type(error.orig).__module__.startswith('psycopg')


LOG = logging.getLogger(__name__)


def log_worker_failure(logger: logging.Logger, message: str, error: BaseException) -> None:
    """Log expected contention as one warning line; keep tracebacks for real bugs."""
    if is_transient_database_error(error):
        reason = str(getattr(error, 'orig', None) or error).strip().splitlines()[0]
        logger.warning("%s (database contention: %s)", message, reason)
    else:
        logger.error(message, exc_info=error)


def retry_transient(operation, *, action: str, attempts: int = 4, retry_on: tuple = ()):
    """Run ``operation`` again after lock timeouts, deadlocks or dropped connections.

    Background writers usually hold expensive results (a crawled site, a parsed
    feed). Retrying the short persistence transaction keeps that work instead
    of discarding it and fetching the source again. Each retry gets a fresh
    transaction, so ``operation`` must open its own session.
    """
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            retryable = isinstance(error, retry_on) or is_transient_database_error(error)
            if not retryable or attempt + 1 == attempts:
                raise
            delay = min(2.0, 0.25 * (2 ** attempt)) + random.uniform(0, 0.15)
            LOG.warning("Contention while %s (%s); retrying in %.2fs (%s/%s)",
                        action, type(getattr(error, 'orig', error)).__name__, delay, attempt + 1, attempts)
            time.sleep(delay)


engine = create_runtime_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
