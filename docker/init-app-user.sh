#!/bin/sh
set -eu

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$HIRING_DB_PASSWORD" <<'SQL'
CREATE ROLE hiring_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE hiring TO hiring_app;
GRANT USAGE ON SCHEMA public TO hiring_app;
SQL
