#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

need_cmds=(git docker node npm python3 pip3)
optional_cmds=(gh)

missing=0

check_cmd() {
  local c="$1"
  if command -v "$c" >/dev/null 2>&1; then
    printf "OK   %-12s %s\n" "$c" "$(command -v "$c")"
  else
    printf "MISS %-12s\n" "$c"
    missing=1
  fi
}

echo "== Team OS Doctor =="
echo "repo_root: $(teamos_root)"
echo

for c in "${need_cmds[@]}"; do
  check_cmd "$c"
done

if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    printf "OK   %-12s %s\n" "docker compose" "$(docker compose version | head -n 1)"
  else
    printf "MISS %-12s\n" "docker compose"
    missing=1
  fi
fi

for c in "${optional_cmds[@]}"; do
  if command -v "$c" >/dev/null 2>&1; then
    printf "OK   %-12s %s\n" "$c" "$(command -v "$c")"
  else
    printf "WARN %-12s (optional)\n" "$c"
  fi
done

echo
if [[ "$missing" -eq 1 ]]; then
  echo "doctor: FAILED (missing required tools)" >&2
  exit 1
fi
echo "doctor: OK"

