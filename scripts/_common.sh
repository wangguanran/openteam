#!/usr/bin/env bash
set -euo pipefail

teamos_root() {
  # scripts -> repo root
  (cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
}

today() {
  date +"%Y-%m-%d"
}

now_utc_iso() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

ts_compact_utc() {
  date -u +"%Y%m%d-%H%M%S"
}

sed_escape_repl() {
  # Escape replacement string for sed with | delimiter.
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

upsert_kv_file() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp
  tmp="$(mktemp "${file}.tmp.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    index($0, key "=") == 1 {
      print key "=" value
      done = 1
      next
    }
    { print }
    END {
      if (!done) {
        print key "=" value
      }
    }
  ' "$file" >"$tmp"
  mv "$tmp" "$file"
}

slugify() {
  # Best-effort ASCII slug: lower, spaces -> -, remove disallowed chars.
  # shellcheck disable=SC2001
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[[:space:]]\+/-/g' -e 's/[^a-z0-9._-]//g' -e 's/--\+/-/g' -e 's/^-//' -e 's/-$//'
}

ensure_dir() {
  local d="$1"
  mkdir -p "$d"
}

teamos_home_dir() {
  if [[ -n "${TEAMOS_HOME:-}" ]]; then
    printf '%s\n' "$TEAMOS_HOME"
  else
    printf '%s/.teamos\n' "$HOME"
  fi
}

default_runtime_config_dir() {
  printf '%s/runtime-config/default\n' "$(teamos_home_dir)"
}

safe_create_file() {
  # Create file only if it doesn't exist.
  # If it exists, do not overwrite; return non-zero.
  local path="$1"
  if [[ -e "$path" ]]; then
    return 1
  fi
  : >"$path"
}
