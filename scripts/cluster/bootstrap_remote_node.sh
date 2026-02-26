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
    [--password-stdin] \
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
PASSWORD_STDIN=0
SSH_PASSWORD=""

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
    --password-stdin) PASSWORD_STDIN=1; shift ;;
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

if [[ "$PASSWORD_STDIN" -eq 1 ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "sshpass is required for --password-stdin mode" >&2
    exit 2
  fi
  IFS= read -r SSH_PASSWORD || true
  if [[ -z "$SSH_PASSWORD" ]]; then
    echo "--password-stdin was set but no password was provided on stdin" >&2
    exit 2
  fi
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

if [[ "$PASSWORD_STDIN" -eq 1 ]]; then
  SSHPASS="$SSH_PASSWORD" sshpass -e scp "${SSH_OPTS[@]}" "scripts/cluster/join_node.sh" "$USER@$HOST:$REMOTE"
  SSHPASS="$SSH_PASSWORD" sshpass -e ssh "${SSH_OPTS[@]}" "$USER@$HOST" "chmod +x \"$REMOTE\" && $JOIN_CMD"
else
  scp "${SSH_OPTS[@]}" "scripts/cluster/join_node.sh" "$USER@$HOST:$REMOTE"
  ssh "${SSH_OPTS[@]}" "$USER@$HOST" "chmod +x \"$REMOTE\" && $JOIN_CMD"
fi
