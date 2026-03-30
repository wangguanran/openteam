#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

action="${1:-start}"
shift || true

# Single-node wrapper; deterministic bootstrap logic lives in scripts/bootstrap_and_run.py
case "$action" in
  start|status|stop|restart|doctor)
    exec "$PY" "$ROOT/scripts/bootstrap_and_run.py" "$action" "$@"
    ;;
  *)
    echo "usage: ./run.sh [start|status|stop|restart|doctor]" >&2
    exit 2
    ;;
esac
