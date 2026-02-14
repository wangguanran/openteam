#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

ROOT="$(teamos_root)"
TASK_ID="${1:-}"
if [[ -z "$TASK_ID" ]]; then
  echo "Usage: ./scripts/teamos.sh retro <TASK_ID>" >&2
  exit 2
fi

logs_dir="$ROOT/.team-os/logs/tasks/$TASK_ID"
if [[ ! -d "$logs_dir" ]]; then
  echo "Task logs dir not found: $logs_dir" >&2
  exit 1
fi

retro_path="$logs_dir/07_retro.md"
if [[ ! -e "$retro_path" ]]; then
  TITLE="$TASK_ID"
  DATE="$(today)"
  sed \
    -e "s|{{TASK_ID}}|$(sed_escape_repl "$TASK_ID")|g" \
    -e "s|{{TITLE}}|$(sed_escape_repl "$TITLE")|g" \
    -e "s|{{DATE}}|$(sed_escape_repl "$DATE")|g" \
    "$ROOT/.team-os/templates/task_log_07_retro.md" >"$retro_path"
fi

echo "retro_log=$retro_path"

