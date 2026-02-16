#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/teamos.sh doctor
  ./scripts/teamos.sh policy-check
  ./scripts/teamos.sh runtime-init [--path <dir>] [--force]
  ./scripts/teamos.sh runtime-secrets [--path <dir>]
  ./scripts/teamos.sh new-task [--full|--short] "<title>"
  ./scripts/teamos.sh skill-boot "<role>" "<topic_or_platform>"
  ./scripts/teamos.sh retro "<task_id>"
  ./scripts/teamos.sh self-improve
EOF
}

auto_wake_self_improve() {
  # Always-on self-improve scheduler wake (non-blocking, debounced in runner).
  # Safe-by-default: dry-run; no remote writes.
  if [[ "${TEAMOS_SELF_IMPROVE_DISABLE:-}" =~ ^(1|true|TRUE|yes|YES)$ ]]; then
    return 0
  fi
  if [[ "${TEAMOS_SELF_IMPROVE_CHILD:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${cmd:-}" == "self-improve" ]]; then
    return 0
  fi

  local root
  root="$(teamos_root)"
  if [[ -x "$root/teamos" ]]; then
    # Ensure local runner can find the repo even if invoked elsewhere.
    # Avoid recursion by marking child.
    (TEAMOS_SELF_IMPROVE_CHILD=1 TEAM_OS_REPO_PATH="$root" "$root/teamos" self-improve --dry-run --auto --quiet --local >/dev/null 2>&1) &
  fi
}

cmd="${1:-}"
shift || true

case "$cmd" in
  doctor)
    auto_wake_self_improve
    exec "$SCRIPT_DIR/doctor.sh" "$@"
    ;;
  policy-check)
    auto_wake_self_improve
    exec "$SCRIPT_DIR/policy_check.sh" "$@"
    ;;
  runtime-init)
    auto_wake_self_improve
    exec "$SCRIPT_DIR/runtime_init.sh" "$@"
    ;;
  runtime-secrets)
    auto_wake_self_improve
    exec "$SCRIPT_DIR/runtime_secrets.sh" "$@"
    ;;
  new-task)
    auto_wake_self_improve
    exec "$SCRIPT_DIR/new_task.sh" "$@"
    ;;
  skill-boot)
    auto_wake_self_improve
    exec "$SCRIPT_DIR/skill_boot.sh" "$@"
    ;;
  retro)
    auto_wake_self_improve
    exec "$SCRIPT_DIR/retro.sh" "$@"
    ;;
  self-improve)
    exec "$SCRIPT_DIR/self_improve.sh" "$@"
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
