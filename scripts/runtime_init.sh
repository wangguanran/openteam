#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/teamos.sh runtime-init [--path <dir>] [--force]

Default:
  --path ../team-os-runtime  (sibling of the team-os repo)

Behavior:
  - Copies runtime template files from templates/runtime into the target directory
  - By default, does NOT overwrite existing files
  - With --force, overwrites existing files with a .bak.<timestamp> backup
EOF
}

ROOT="$(teamos_root)"
TEMPLATE_DIR="$ROOT/templates/runtime"

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
  target="$ROOT/../team-os-runtime"
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

while IFS= read -r -d '' src; do
  rel="${src#"$TEMPLATE_DIR/"}"
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
done < <(find "$TEMPLATE_DIR" -type f -print0)

echo
echo "runtime_path=$target"
echo "copied_files=$copied"
echo "skipped_files=$skipped"
echo "overwritten_files=$overwritten"
echo
echo "next:"
echo "  cd \"$target\""
echo "  cp .env.example .env"
echo "  # optional: generate local secrets (no output):"
echo "  #   cd \"$ROOT\" && ./scripts/teamos.sh runtime-secrets --path \"$target\""
echo "  make up"
echo "  make ps"

