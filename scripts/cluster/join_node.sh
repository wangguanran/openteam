#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Join this machine to a Team OS cluster (assistant node).

This script is idempotent and does NOT store secrets.

Prereqs (install manually if missing):
  - curl
  - python3 (for uuid generation)

Usage:
  bash scripts/cluster/join_node.sh \
    --cluster-repo owner/name \
    --brain-base-url http://<brain-host>:8787 \
    --role assistant|auto \
    --capabilities "repo_rw,docker" \
    --tags "site:bj,device:no"

Notes:
  - This script registers the node to the Brain control-plane via /v1/nodes/register and starts heartbeats.
  - Cluster GitHub Issues bus and full runtime deployment are handled separately (see docs/runbooks/NODE_BOOTSTRAP.md).
EOF
}

CLUSTER_REPO=""
BRAIN_BASE_URL=""
ROLE="auto"
CAPS=""
TAGS=""
NODE_DIR="${TEAMOS_NODE_DIR:-/opt/team-os-node}"
HEARTBEAT_INTERVAL_SEC="${TEAMOS_NODE_HEARTBEAT_INTERVAL_SEC:-60}"

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

mkdir -p "$NODE_DIR"

INSTANCE_ID_PATH="$NODE_DIR/instance_id"
if [[ -f "$INSTANCE_ID_PATH" && -s "$INSTANCE_ID_PATH" ]]; then
  INSTANCE_ID="$(cat "$INSTANCE_ID_PATH" | tr -d '\n' | tr -d '\r')"
else
  if command -v python3 >/dev/null 2>&1; then
    INSTANCE_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  else
    echo "python3 is required to generate instance_id" >&2
    exit 1
  fi
  echo "$INSTANCE_ID" >"$INSTANCE_ID_PATH"
fi

# Resources (best-effort)
CPU_CORES="$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)"
MEM_GB="$(python3 - <<'PY' 2>/dev/null || echo 0
import os, sys
try:
  import psutil
  print(round(psutil.virtual_memory().total/1024/1024/1024,2))
except Exception:
  print(0)
PY
)"

CAPS_JSON="[]"
if [[ -n "$CAPS" ]]; then
  # split by comma
  CAPS_JSON="$(python3 - <<PY
import json
caps = [c.strip() for c in "${CAPS}".split(",") if c.strip()]
print(json.dumps(caps, ensure_ascii=False))
PY
)"
fi

TAGS_JSON="[]"
if [[ -n "$TAGS" ]]; then
  TAGS_JSON="$(python3 - <<PY
import json
tags = [t.strip() for t in "${TAGS}".split(",") if t.strip()]
print(json.dumps(tags, ensure_ascii=False))
PY
)"
fi

PAYLOAD="$(python3 - <<PY
import json
payload = {
  "instance_id": "${INSTANCE_ID}",
  "role_preference": "${ROLE}",
  "base_url": "",
  "capabilities": json.loads('${CAPS_JSON}'),
  "resources": {"cpu_cores": int(float("${CPU_CORES}") or 1), "mem_gb": float("${MEM_GB}") or 0.0},
  "agent_policy": {"max_agents": 0, "soft_limits": {}},
  "tags": json.loads('${TAGS_JSON}'),
}
print(json.dumps(payload, ensure_ascii=False))
PY
)"

echo "registering node instance_id=$INSTANCE_ID to brain=$BRAIN_BASE_URL ..."
curl -fsS -X POST "$BRAIN_BASE_URL/v1/nodes/register" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" >/dev/null
echo "registered: OK"

# Heartbeat loop (no secrets). If systemd exists, prefer a service; otherwise use nohup.
HB_CMD="curl -fsS -X POST \"$BRAIN_BASE_URL/v1/nodes/heartbeat\" -H \"Content-Type: application/json\" -d '{\"instance_id\":\"$INSTANCE_ID\"}' >/dev/null"

if command -v systemctl >/dev/null 2>&1; then
  SERVICE_PATH="/etc/systemd/system/teamos-node-heartbeat.service"
  if [[ ! -f "$SERVICE_PATH" ]]; then
    cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=Team OS Node Heartbeat
After=network-online.target

[Service]
Type=simple
ExecStart=/bin/bash -lc 'while true; do $HB_CMD; sleep $HEARTBEAT_INTERVAL_SEC; done'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable teamos-node-heartbeat.service >/dev/null || true
  fi
  systemctl restart teamos-node-heartbeat.service
  systemctl status teamos-node-heartbeat.service --no-pager -n 3 || true
else
  # fallback
  nohup bash -lc "while true; do $HB_CMD; sleep $HEARTBEAT_INTERVAL_SEC; done" >/dev/null 2>&1 &
fi

echo "done."
echo "next:"
echo "  - verify on brain: teamos cluster status"
