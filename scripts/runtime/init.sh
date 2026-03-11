#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../_common.sh"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/teamos.sh runtime-init [--path <dir>] [--force]

Default:
  --path ~/.teamos/runtime-config/default

Behavior:
  - Copies only runtime deployment config files into the runtime config directory
  - By default, does NOT overwrite existing files
  - With --force, overwrites existing files with a .bak.<timestamp> backup
  - Seeds TEAM_OS_REPO_PATH and docker project defaults in .env.example
EOF
}

ROOT="$(teamos_root)"
TEMPLATE_DIR="$ROOT/scaffolds/runtime"

target=""
force=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --path)
      target="${2:-}"
      shift 2
      ;;
    --force)
      force=1
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

if [[ -z "$target" ]]; then
  target="$(default_runtime_config_dir)"
fi

if [[ ! -d "$TEMPLATE_DIR" ]]; then
  echo "Runtime template dir not found: $TEMPLATE_DIR" >&2
  exit 1
fi

ensure_dir "$target"

ts="$(ts_compact_utc)"
copied=0
skipped=0
overwritten=0

copy_paths=(
  "Makefile"
  "README.md"
  ".gitignore"
  ".env.example"
  "docker-compose.yml"
  "scripts/auto_update.py"
)

for rel in "${copy_paths[@]}"; do
  src="$TEMPLATE_DIR/$rel"
  if [[ ! -f "$src" ]]; then
    echo "Missing template file: $rel" >&2
    exit 1
  fi
  dest="$target/$rel"
  ensure_dir "$(dirname "$dest")"

  if [[ -e "$dest" ]]; then
    if [[ "$force" -eq 1 ]]; then
      bak="$dest.bak.$ts"
      cp -p "$dest" "$bak"
      cp -p "$src" "$dest"
      echo "overwritten: $rel (backup: ${rel}.bak.$ts)"
      overwritten=$((overwritten + 1))
    else
      echo "skip (exists): $rel"
      skipped=$((skipped + 1))
    fi
    continue
  fi

  cp -p "$src" "$dest"
  echo "created: $rel"
  copied=$((copied + 1))
done

env_example="$target/.env.example"
if [[ -f "$env_example" ]]; then
  base_name="$(basename "$target")"
  if [[ "$base_name" == "default" ]]; then
    project_name="team-os-runtime"
  else
    project_name="teamos-$(slugify "$base_name")"
    if [[ -z "$project_name" || "$project_name" == "teamos-" ]]; then
      project_name="team-os-runtime"
    fi
  fi
  upsert_kv_file "$env_example" "TEAM_OS_REPO_PATH" "$ROOT"
  upsert_kv_file "$env_example" "TEAMOS_DOCKER_PROJECT_NAME" "$project_name"
  upsert_kv_file "$env_example" "TEAMOS_DOCKER_VOLUME_PREFIX" "$project_name"
fi

echo
echo "runtime_config_path=$target"
echo "copied_files=$copied"
echo "skipped_files=$skipped"
echo "overwritten_files=$overwritten"
echo
echo "next:"
echo "  cd \"$target\""
echo "  cp .env.example .env"
echo "  # optional: generate local secrets (no output):"
echo "  #   cd \"$ROOT\" && ./scripts/teamos.sh runtime-secrets --path \"$target\""
echo "  # runtime state/tmp/cache will live in Docker named volumes"
echo "  make up"
echo "  make ps"
