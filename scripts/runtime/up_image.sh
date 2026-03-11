#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../_common.sh"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/runtime_up_image.sh [--db-url <postgres-dsn>] [--path <dir>] [--image <ref>] [--port <port>] [--force] [--skip-pull]

Purpose:
  Initialize an image-based team-os-runtime deployment, write the required .env values,
  pull the published control-plane image, and start the runtime with the unified docker compose file.

Defaults:
  --path  ../team-os-runtime-image
  --image ghcr.io/wangguanran/teamos-control-plane:main
  --port  8787
  --db-url unset (control-plane uses local postgres)
EOF
}

ROOT="$(teamos_root)"
target="${ROOT}/../team-os-runtime-image"
db_url=""
image="ghcr.io/wangguanran/teamos-control-plane:main"
port="8787"
force=0
skip_pull=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db-url)
      db_url="${2:-}"
      shift 2
      ;;
    --path)
      target="${2:-}"
      shift 2
      ;;
    --image)
      image="${2:-}"
      shift 2
      ;;
    --port)
      port="${2:-}"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    --skip-pull)
      skip_pull=1
      shift
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

ensure_dir "$target"

init_args=(--path "$target")
if [[ "$force" -eq 1 ]]; then
  init_args+=(--force)
fi
"$ROOT/scripts/runtime/init.sh" "${init_args[@]}"

env_file="$target/.env"
if [[ ! -f "$env_file" ]]; then
  cp "$target/.env.example" "$env_file"
fi

upsert_env() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(sed_escape_repl "$value")"
  if grep -qE "^${key}=" "$env_file"; then
    sed -i.bak -E "s|^${key}=.*$|${key}=${escaped}|" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$env_file"
  fi
}

upsert_env "TEAMOS_DB_URL" "$db_url"
upsert_env "TEAMOS_CONTROL_PLANE_IMAGE" "$image"
upsert_env "CONTROL_PLANE_PORT" "$port"
upsert_env "TEAMOS_RUNTIME_FILE_MIRROR" "0"

(
  cd "$target"
  if [[ "$skip_pull" -ne 1 ]]; then
    docker compose pull
  fi
  docker compose up -d --no-build
  python3 ./scripts/auto_update.py start --runtime-dir . >/dev/null
)

echo
echo "runtime_path=$target"
echo "image=$image"
if [[ -n "$db_url" ]]; then
  echo "database_mode=external"
  echo "database_url=$db_url"
else
  echo "database_mode=localdb"
fi
echo "base_url=http://127.0.0.1:${port}"
echo "next:"
echo "  cd \"$target\" && docker compose ps"
echo "  curl -fsS http://127.0.0.1:${port}/healthz"
