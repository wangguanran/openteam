#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../_common.sh"

ROOT="$(teamos_root)"
ENTRY_PATH="${1:-}"

if [[ -z "$ENTRY_PATH" ]]; then
  echo "Usage: ./scripts/open_issue.sh <team_workflow_entry.md>" >&2
  exit 2
fi

if [[ ! -f "$ENTRY_PATH" ]]; then
  echo "File not found: $ENTRY_PATH" >&2
  exit 1
fi

title="$(head -n 1 "$ENTRY_PATH" | sed -e 's/^# *//')"
if [[ -z "$title" ]]; then
  title="Team OS Team Workflow"
fi

pending_dir="$ROOT/.team-os/ledger/team_os_issues_pending"
ensure_dir "$pending_dir"

fallback() {
  local ts slug out
  ts="$(ts_compact_utc)"
  slug="$(slugify "$title")"
  [[ -z "$slug" ]] && slug="team-workflow"
  out="$pending_dir/${ts}_${slug}.md"
  cat >"$out" <<EOF
# Pending Issue Draft

- created_at: $(now_utc_iso)
- title: $title
- source_entry: ${ENTRY_PATH#$ROOT/}

## Body

$(cat "$ENTRY_PATH")
EOF
  echo "pending_issue_draft=$out"
}

if ! command -v gh >/dev/null 2>&1; then
  echo "gh not found; generating pending issue draft instead." >&2
  fallback
  exit 0
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh not authenticated; generating pending issue draft instead." >&2
  fallback
  exit 0
fi

if ! (cd "$ROOT" && git remote get-url origin >/dev/null 2>&1); then
  echo "git remote 'origin' not set; generating pending issue draft instead." >&2
  echo "hint: set remote in $ROOT, then rerun." >&2
  fallback
  exit 0
fi

body_tmp="$(mktemp)"
trap 'rm -f "$body_tmp"' EXIT
cat "$ENTRY_PATH" >"$body_tmp"

echo "creating_github_issue_title=$title"
(cd "$ROOT" && gh issue create --title "$title" --body-file "$body_tmp") || {
  echo "gh issue create failed; generating pending issue draft instead." >&2
  fallback
  exit 0
}
