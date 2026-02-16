#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Bootstrap a remote node via SSH by copying and executing join_node.sh.

Safety:
  - Default is dry-run (prints the commands).
  - No secrets are stored. Passwords must NOT be passed via CLI args.

Usage:
  bash scripts/cluster/bootstrap_remote_node.sh \
    --host <ip> --user <user> \
    --cluster-repo owner/name \
    --brain-base-url http://<brain-host>:8787 \
    --role assistant|auto \
    --capabilities "repo_rw,docker" \
    --tags "site:bj,device:no" \
    [--ssh-key <path>] \
    [--execute]
EOF
}

HOST=""
USER=""
SSH_KEY=""
CLUSTER_REPO=""
BRAIN_BASE_URL=""
ROLE="auto"
CAPS=""
TAGS=""
EXECUTE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --user) USER="${2:-}"; shift 2 ;;
    --ssh-key) SSH_KEY="${2:-}"; shift 2 ;;
    --cluster-repo) CLUSTER_REPO="${2:-}"; shift 2 ;;
    --brain-base-url) BRAIN_BASE_URL="${2:-}"; shift 2 ;;
    --role) ROLE="${2:-auto}"; shift 2 ;;
    --capabilities) CAPS="${2:-}"; shift 2 ;;
    --tags) TAGS="${2:-}"; shift 2 ;;
    --execute) EXECUTE=1; shift ;;
    -h|--help|help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$HOST" || -z "$USER" || -z "$CLUSTER_REPO" || -z "$BRAIN_BASE_URL" ]]; then
  echo "Missing required args" >&2
  usage >&2
  exit 2
fi

SSH_OPTS=()
if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTS+=("-i" "$SSH_KEY")
fi

REMOTE="/tmp/teamos_join_node.sh"
JOIN_CMD="bash $REMOTE --cluster-repo \"$CLUSTER_REPO\" --brain-base-url \"$BRAIN_BASE_URL\" --role \"$ROLE\" --capabilities \"$CAPS\" --tags \"$TAGS\""

echo "plan:"
echo "  scp scripts/cluster/join_node.sh -> $USER@$HOST:$REMOTE"
echo "  ssh $USER@$HOST \"$JOIN_CMD\""

if [[ "$EXECUTE" -ne 1 ]]; then
  echo
  echo "dry-run: not executing. Add --execute to run."
  exit 0
fi

scp "${SSH_OPTS[@]}" "scripts/cluster/join_node.sh" "$USER@$HOST:$REMOTE"
ssh "${SSH_OPTS[@]}" "$USER@$HOST" "chmod +x \"$REMOTE\" && $JOIN_CMD"

