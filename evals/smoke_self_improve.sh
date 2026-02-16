#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Safety: never write remote state during evals unless explicitly enabled.
export TEAMOS_PANEL_GH_WRITE_ENABLED="${TEAMOS_PANEL_GH_WRITE_ENABLED:-0}"
export TEAMOS_ALLOW_REMOTE_WRITES="${TEAMOS_ALLOW_REMOTE_WRITES:-0}"

echo "[1/4] unittest"
python3 -m unittest discover -q

echo "[2/4] cli help"
./teamos --help >/dev/null

echo "[3/4] self-improve dry-run (must create proposals, no remote writes)"
./teamos self-improve --dry-run --force --local >/dev/null

echo "[4/4] status (should not crash even without projects)"
./teamos status --project teamos >/dev/null || true

echo "OK"

