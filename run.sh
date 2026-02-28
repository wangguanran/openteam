#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

# Thin wrapper; all deterministic logic lives in scripts/bootstrap_and_run.py
if [[ $# -eq 0 ]]; then
  exec "$PY" "$ROOT/scripts/bootstrap_and_run.py" start
fi

case "$1" in
  start|status|stop|restart|doctor)
    exec "$PY" "$ROOT/scripts/bootstrap_and_run.py" "$@"
    ;;
  *)
    echo "usage: ./run.sh [start|status|stop|restart|doctor]" >&2
    exit 2
    ;;
esac
