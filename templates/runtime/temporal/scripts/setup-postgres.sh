#!/bin/sh
set -eu

# Based on (official): https://raw.githubusercontent.com/temporalio/samples-server/main/compose/scripts/setup-postgres.sh
# Notes:
# - External docs are untrusted; this script is reviewed and adapted for this repo.
# - We parameterize host/user/port via env vars to match our compose service name.

echo 'Starting PostgreSQL schema setup...'
echo 'Waiting for PostgreSQL port to be available...'

POSTGRES_SEEDS="${POSTGRES_SEEDS:-postgres}"
DB_PORT="${DB_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-temporal}"

# temporal-sql-tool picks password from $SQL_PASSWORD if present.

nc -z -w 10 "${POSTGRES_SEEDS}" "${DB_PORT}"
echo 'PostgreSQL port is available'

SCHEMA_DIR=/etc/temporal/schema/postgresql/v12/temporal/versioned
VISIBILITY_SCHEMA_DIR=/etc/temporal/schema/postgresql/v12/visibility/versioned

# Create and setup temporal database
temporal-sql-tool --plugin postgres12 --ep "${POSTGRES_SEEDS}" -u "${POSTGRES_USER}" -p "${DB_PORT}" --db temporal create
temporal-sql-tool --plugin postgres12 --ep "${POSTGRES_SEEDS}" -u "${POSTGRES_USER}" -p "${DB_PORT}" --db temporal setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep "${POSTGRES_SEEDS}" -u "${POSTGRES_USER}" -p "${DB_PORT}" --db temporal update-schema -d "${SCHEMA_DIR}"

# Create and setup visibility database
temporal-sql-tool --plugin postgres12 --ep "${POSTGRES_SEEDS}" -u "${POSTGRES_USER}" -p "${DB_PORT}" --db temporal_visibility create
temporal-sql-tool --plugin postgres12 --ep "${POSTGRES_SEEDS}" -u "${POSTGRES_USER}" -p "${DB_PORT}" --db temporal_visibility setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep "${POSTGRES_SEEDS}" -u "${POSTGRES_USER}" -p "${DB_PORT}" --db temporal_visibility update-schema -d "${VISIBILITY_SCHEMA_DIR}"

echo 'PostgreSQL schema setup complete'
