#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

ROOT="$(teamos_root)"
TITLE="${1:-}"
if [[ -z "$TITLE" ]]; then
  echo "Usage: ./scripts/teamos.sh new-task \"<title>\"" >&2
  exit 2
fi

DATE="$(today)"
NOW_ISO="$(now_utc_iso)"

ensure_dir "$ROOT/.team-os/ledger/tasks"
ensure_dir "$ROOT/.team-os/logs/tasks"

task_id=""
for _ in 1 2 3 4 5; do
  cand="TASK-$(ts_compact_utc)"
  if [[ ! -e "$ROOT/.team-os/ledger/tasks/$cand.yaml" ]]; then
    task_id="$cand"
    break
  fi
  sleep 1
done

if [[ -z "$task_id" ]]; then
  echo "Failed to generate unique task id" >&2
  exit 1
fi

logs_dir="$ROOT/.team-os/logs/tasks/$task_id"
ensure_dir "$logs_dir"

render_log() {
  local tpl="$1"
  local out="$2"
  if [[ -e "$out" ]]; then
    echo "Refusing to overwrite: $out" >&2
    return 1
  fi
  sed \
    -e "s|{{TASK_ID}}|$(sed_escape_repl "$task_id")|g" \
    -e "s|{{TITLE}}|$(sed_escape_repl "$TITLE")|g" \
    -e "s|{{DATE}}|$(sed_escape_repl "$DATE")|g" \
    "$tpl" >"$out"
}

ledger_out="$ROOT/.team-os/ledger/tasks/$task_id.yaml"
if [[ -e "$ledger_out" ]]; then
  echo "Refusing to overwrite: $ledger_out" >&2
  exit 1
fi

sed \
  -e "s|<TASK_ID>|$(sed_escape_repl "$task_id")|g" \
  -e "s|<TITLE>|$(sed_escape_repl "$TITLE")|g" \
  -e "s|<YYYY-MM-DDTHH:MM:SSZ>|$(sed_escape_repl "$NOW_ISO")|g" \
  "$ROOT/.team-os/templates/task_ledger.yaml" >"$ledger_out"

render_log "$ROOT/.team-os/templates/task_log_00_intake.md" "$logs_dir/00_intake.md"
render_log "$ROOT/.team-os/templates/task_log_01_plan.md" "$logs_dir/01_plan.md"
render_log "$ROOT/.team-os/templates/task_log_02_todo.md" "$logs_dir/02_todo.md"

echo "created_task_id=$task_id"
echo "ledger=$ledger_out"
echo "logs_dir=$logs_dir"

