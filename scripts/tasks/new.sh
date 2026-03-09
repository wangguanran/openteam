#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../_common.sh"

ROOT="$(teamos_root)"
# Default policy: always create full 00~07 logs + metrics.jsonl (compliance requirement).
# `--short` is available for special cases; `--full` is accepted as a no-op alias.
FULL=1
if [[ "${1:-}" == "--short" ]]; then
  FULL=0
  shift
elif [[ "${1:-}" == "--full" ]]; then
  FULL=1
  shift
fi

TITLE="${1:-}"
if [[ -z "$TITLE" ]]; then
  echo "Usage: ./scripts/teamos.sh new-task [--full|--short] \"<title>\"" >&2
  exit 2
fi
shift || true

# Allow `--short`/`--full` after title as well.
if [[ "${1:-}" == "--short" ]]; then
  FULL=0
  shift
elif [[ "${1:-}" == "--full" ]]; then
  FULL=1
  shift
fi
if [[ "${1:-}" != "" ]]; then
  echo "Unexpected argument: $1" >&2
  echo "Usage: ./scripts/teamos.sh new-task [--full|--short] \"<title>\"" >&2
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
  "$ROOT/templates/tasks/task_ledger.yaml" >"$ledger_out"

render_log "$ROOT/templates/tasks/task_log_00_intake.md" "$logs_dir/00_intake.md"
render_log "$ROOT/templates/tasks/task_log_01_plan.md" "$logs_dir/01_plan.md"
render_log "$ROOT/templates/tasks/task_log_02_todo.md" "$logs_dir/02_todo.md"

if [[ "$FULL" -eq 1 ]]; then
  render_log "$ROOT/templates/tasks/task_log_03_work.md" "$logs_dir/03_work.md"
  render_log "$ROOT/templates/tasks/task_log_04_test.md" "$logs_dir/04_test.md"
  render_log "$ROOT/templates/tasks/task_log_05_release.md" "$logs_dir/05_release.md"
  render_log "$ROOT/templates/tasks/task_log_06_observe.md" "$logs_dir/06_observe.md"
  render_log "$ROOT/templates/tasks/task_log_07_retro.md" "$logs_dir/07_retro.md"
fi

# metrics.jsonl (telemetry; must never include secrets)
metrics_out="$logs_dir/metrics.jsonl"
if [[ -e "$metrics_out" ]]; then
  echo "Refusing to overwrite: $metrics_out" >&2
  exit 1
fi
cat >"$metrics_out" <<EOF
{"ts":"$NOW_ISO","event_type":"TASK_CREATED","actor":"teamos.sh","task_id":"$task_id","project_id":"teamos","workstream_id":"general","severity":"INFO","message":"task scaffold created","payload":{"ledger":"$ledger_out","logs_dir":"$logs_dir"}}
EOF

echo "created_task_id=$task_id"
echo "ledger=$ledger_out"
echo "logs_dir=$logs_dir"
