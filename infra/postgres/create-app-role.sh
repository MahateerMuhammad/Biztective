#!/bin/sh
# Creates (or re-passwords) the least-privilege runtime role `root_app`.
# Runs automatically in the Docker image's init phase, and is reused by CI and test setup.
# Connection comes from standard libpq variables (PGHOST, PGUSER, PGDATABASE, ...),
# executed as the schema-owner role. The password is read from ROOT_APP_DB_PASSWORD only.
set -eu

: "${ROOT_APP_DB_PASSWORD:?ROOT_APP_DB_PASSWORD must be set}"
# Inside the postgres image's init phase only POSTGRES_USER/POSTGRES_DB are set.
export PGUSER="${PGUSER:-${POSTGRES_USER:-postgres}}"
export PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-postgres}}"

psql -v ON_ERROR_STOP=1 -v app_password="$ROOT_APP_DB_PASSWORD" <<'SQL'
SELECT format(
    'CREATE ROLE root_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE',
    :'app_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'root_app')
\gexec
SELECT format('ALTER ROLE root_app WITH LOGIN PASSWORD %L', :'app_password')
\gexec
SQL
