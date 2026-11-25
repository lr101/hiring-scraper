"""Create the disposable PostgreSQL database named by TEST_POSTGRES_URL if it is missing.

The CI quality container runs the unit suite against the stack's PostgreSQL
image. The database name must start with ``hiring_test`` or ``radius_test`` so a
misconfigured URL can never point the suite at a real database.
"""
from __future__ import annotations

import os
import re
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main() -> int:
    target = make_url(os.environ.get('TEST_POSTGRES_URL', ''))
    name = target.database or ''
    if target.get_backend_name() != 'postgresql' or not re.fullmatch(r'(hiring|radius)_test\w*', name):
        print('TEST_POSTGRES_URL must name a disposable hiring_test*/radius_test* database.', file=sys.stderr)
        return 2
    admin = create_engine(target.set(database='postgres'), isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as connection:
            exists = connection.scalar(text('SELECT 1 FROM pg_database WHERE datname = :name'), {'name': name})
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    finally:
        admin.dispose()
    print(f'Test database ready: {name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
