#!/usr/bin/env bash
# Run PostgreSQL regressions against a uniquely named, disposable database.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
if [[ ${HIRING_LOCAL_POSTGRES:-} != 1 ]]; then
    printf '%s\n' 'Set HIRING_LOCAL_POSTGRES=1 and configure libpq PGHOST/PGPORT/PGUSER before running.' >&2
    exit 2
fi
command -v createdb >/dev/null
command -v dropdb >/dev/null
command -v psql >/dev/null
command -v uv >/dev/null
if [[ $(psql -X -d postgres -Atqc "SELECT count(*) FROM pg_roles WHERE rolname = 'hiring_app'") != 1 ]]; then
    printf '%s\n' 'The local PostgreSQL server needs the hiring_app role for migration grants; see docs/ci.md.' >&2
    exit 1
fi
db_name="hiring_ci_$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
createdb --maintenance-db=postgres --template=template0 "$db_name"
cleanup() {
    local status=$?
    trap - EXIT
    if ! dropdb --maintenance-db=postgres "$db_name"; then
        printf 'Could not remove disposable database %s\n' "$db_name" >&2
        status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export TEST_POSTGRES_URL="postgresql+psycopg:///$db_name"
export DATABASE_URL="$TEST_POSTGRES_URL"
printf '%s\n' 'Running PostgreSQL concurrency regressions'
uv run python -m unittest discover -s tests -p test_postgres_concurrency.py -v
printf '%s\n' 'Running PostgreSQL migrations (requires PostGIS and pgvector)'
uv run alembic upgrade head
uv run python -c 'from alembic.config import Config; from sqlalchemy import create_engine; from scripts.ci.migration_heads import assert_migration_heads; import os; engine = create_engine(os.environ["DATABASE_URL"]); connection = engine.connect(); assert_migration_heads(connection, Config("alembic.ini")); connection.close(); engine.dispose()'
