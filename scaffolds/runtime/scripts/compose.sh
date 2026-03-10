#!/usr/bin/env bash
set -euo pipefail

has_external_db=0
if [[ -n "${TEAMOS_DB_URL:-}" ]]; then
  has_external_db=1
elif [[ -f .env ]] && grep -qE '^TEAMOS_DB_URL=.+$' .env; then
  has_external_db=1
fi

if [[ "$has_external_db" -eq 1 ]]; then
  exec docker compose "$@"
fi

profiles="${COMPOSE_PROFILES:-}"
if [[ ",${profiles}," != *",localdb,"* ]]; then
  if [[ -n "$profiles" ]]; then
    profiles="${profiles},localdb"
  else
    profiles="localdb"
  fi
fi

exec env COMPOSE_PROFILES="$profiles" docker compose "$@"
