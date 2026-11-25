#!/usr/bin/env bash
# Run the PostgreSQL checks against a throwaway cluster owned by this shell.
#
# Needs PostgreSQL server binaries (initdb, pg_ctl) with PostGIS and pgvector
# installed; no root, Docker or existing server is required. The cluster lives
# in a private temporary directory, listens only on 127.0.0.1 with a random
# password, and is deleted on exit. Extra arguments replace the default command.
#
#   scripts/dev/postgres-sandbox.sh                         # full test suite + migration check
#   scripts/dev/postgres-sandbox.sh bash scripts/ci/check.sh  # host quality checks
#   scripts/dev/postgres-sandbox.sh uv run python -m unittest discover -s tests -p test_seed.py
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

bindir=${PG_BINDIR:-}
if [[ -z $bindir ]]; then
    if command -v pg_config >/dev/null; then
        bindir=$(pg_config --bindir)
    else
        bindir=$(find /usr/lib/postgresql -maxdepth 2 -name bin -type d 2>/dev/null | sort -V | tail -1)
    fi
fi
if [[ ! -x $bindir/initdb ]]; then
    printf '%s\n' 'PostgreSQL server binaries not found; set PG_BINDIR to the directory containing initdb.' >&2
    exit 2
fi

root=$(mktemp -d "${TMPDIR:-/tmp}/radius-pg.XXXXXX")
chmod 700 "$root"
cleanup() {
    "$bindir/pg_ctl" -D "$root/data" -m immediate stop >/dev/null 2>&1 || true
    rm -rf "$root"
}
trap cleanup EXIT

od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > "$root/password"
port=$(( 20000 + $(od -An -N2 -tu2 /dev/urandom | tr -d ' ') % 20000 ))
"$bindir/initdb" -D "$root/data" -U postgres --auth=scram-sha-256 --pwfile="$root/password" \
    -E UTF8 --locale=C.UTF-8 >"$root/initdb.log"
"$bindir/pg_ctl" -D "$root/data" -l "$root/server.log" -w \
    -o "-p $port -c listen_addresses=127.0.0.1 -c unix_socket_directories=''" start >/dev/null
printf '127.0.0.1:%s:*:postgres:%s\n' "$port" "$(cat "$root/password")" > "$root/pgpass"
chmod 600 "$root/pgpass"
export PGHOST=127.0.0.1 PGPORT=$port PGUSER=postgres PGPASSFILE=$root/pgpass

missing=$(psql -X -d postgres -Atqc "SELECT string_agg(name, ', ') FROM unnest(ARRAY['postgis', 'vector']) AS name
    WHERE name NOT IN (SELECT name FROM pg_available_extensions)")
if [[ -n $missing ]]; then
    printf 'Install the PostgreSQL extensions first: %s (see docs/ci.md).\n' "$missing" >&2
    exit 2
fi
psql -X -q -d postgres -c 'CREATE ROLE hiring_app NOLOGIN'
printf 'Disposable PostgreSQL %s on 127.0.0.1:%s\n' "$("$bindir/postgres" --version | awk '{print $3}')" "$port"

if [[ $# -gt 0 ]]; then
    # Commands get a disposable test database, e.g. scripts/ci/check.sh or one test module.
    createdb --template=template0 radius_test
    export TEST_POSTGRES_URL="postgresql+psycopg://postgres@127.0.0.1:$port/radius_test"
    export DATABASE_URL="$TEST_POSTGRES_URL"
    "$@"
else
    HIRING_LOCAL_POSTGRES=1 bash scripts/ci/postgres-local.sh
fi
