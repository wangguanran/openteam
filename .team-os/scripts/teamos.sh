#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/teamos.sh doctor
  ./scripts/teamos.sh new-task "<title>"
  ./scripts/teamos.sh skill-boot "<role>" "<topic_or_platform>"
  ./scripts/teamos.sh retro "<task_id>"
  ./scripts/teamos.sh self-improve
EOF
}

cmd="${1:-}"
shift || true

case "$cmd" in
  doctor)
    exec "$SCRIPT_DIR/doctor.sh" "$@"
    ;;
  new-task)
    exec "$SCRIPT_DIR/new_task.sh" "$@"
    ;;
  skill-boot)
    exec "$SCRIPT_DIR/skill_boot.sh" "$@"
    ;;
  retro)
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

