#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Print a one-liner to join a node to the OpenTeam cluster.

This does NOT include passwords/keys/tokens.

Usage:
  bash scripts/cluster/print_join_oneliner.sh \
    --cluster-repo owner/name \
    --brain-base-url http://<brain-host>:8787 \
    --role assistant|auto \
    --capabilities "repo_rw,docker" \
    --tags "site:bj,device:no"

Output:
  A command you can paste/run on the new server AFTER it has this repo available.
EOF
}

CLUSTER_REPO=""
BRAIN_BASE_URL=""
ROLE="auto"
CAPS=""
TAGS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cluster-repo) CLUSTER_REPO="${2:-}"; shift 2 ;;
    --brain-base-url) BRAIN_BASE_URL="${2:-}"; shift 2 ;;
    --role) ROLE="${2:-auto}"; shift 2 ;;
    --capabilities) CAPS="${2:-}"; shift 2 ;;
    --tags) TAGS="${2:-}"; shift 2 ;;
    -h|--help|help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$CLUSTER_REPO" || -z "$BRAIN_BASE_URL" ]]; then
  echo "Missing required args: --cluster-repo and --brain-base-url" >&2
  usage >&2
  exit 2
fi

printf '%s\n' "bash scripts/cluster/join_node.sh --cluster-repo \"$CLUSTER_REPO\" --brain-base-url \"$BRAIN_BASE_URL\" --role \"$ROLE\" --capabilities \"$CAPS\" --tags \"$TAGS\""

