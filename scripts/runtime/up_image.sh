#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../_common.sh"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/runtime_up_image.sh [--db-url <postgres-dsn>] [--path <dir>] [--image <ref>] [--port <port>] [--force] [--skip-pull]

Purpose:
  Initialize an image-based TeamOS runtime config directory, write the required .env values,
  pull the published control-plane image created by GitHub CI, and start the runtime.

Defaults:
  --path  ~/.teamos/runtime-config/default
  --image ghcr.io/wangguanran/teamos-control-plane:main
  --port  8787
  --db-url unset (control-plane uses local postgres)
EOF
}

ROOT="$(teamos_root)"
target="$(default_runtime_config_dir)"
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
  upsert_kv_file "$env_file" "$key" "$value"
}

upsert_env "TEAMOS_DB_URL" "$db_url"
upsert_env "TEAMOS_CONTROL_PLANE_IMAGE" "$image"
upsert_env "CONTROL_PLANE_PORT" "$port"
upsert_env "TEAMOS_RUNTIME_FILE_MIRROR" "0"
upsert_env "TEAM_OS_REPO_PATH" "$ROOT"

base_name="$(basename "$target")"
if [[ "$base_name" == "default" ]]; then
  project_name="team-os-runtime"
else
  project_name="teamos-$(slugify "$base_name")"
  if [[ -z "$project_name" || "$project_name" == "teamos-" ]]; then
    project_name="team-os-runtime"
  fi
fi
upsert_env "TEAMOS_DOCKER_PROJECT_NAME" "$project_name"
upsert_env "TEAMOS_DOCKER_VOLUME_PREFIX" "$project_name"

(
  cd "$target"
  if [[ "$skip_pull" -ne 1 ]]; then
    docker compose pull
  fi
  docker compose up -d --no-build
  python3 ./scripts/auto_update.py start --runtime-dir . >/dev/null
)

echo
echo "runtime_config_path=$target"
echo "image=$image"
if [[ -n "$db_url" ]]; then
  echo "database_mode=external"
  echo "database_url=$db_url"
else
  echo "database_mode=localdb"
fi
echo "runtime_data_mode=docker_named_volumes"
echo "base_url=http://127.0.0.1:${port}"
echo "next:"
echo "  cd \"$target\" && docker compose ps"
echo "  curl -fsS http://127.0.0.1:${port}/healthz"
