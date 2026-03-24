#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ROOT/scripts/_common.sh"

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

echo "== OpenTeam Doctor =="
echo "repo_root: $(openteam_root)"
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

if command -v gh >/dev/null 2>&1; then
  if gh auth status >/dev/null 2>&1; then
    printf "OK   %-12s %s\n" "gh auth" "authenticated"
  else
    printf "WARN %-12s %s\n" "gh auth" "not authenticated (run: gh auth login -h github.com)"
  fi
fi

echo
if [[ "$missing" -eq 1 ]]; then
  echo "doctor: FAILED (missing required tools)" >&2
  exit 1
fi

# Repo purity checks (no project truth-source artifacts inside repo).
if python3 "$ROOT/scripts/governance/check_repo_purity.py" --quiet >/dev/null 2>&1; then
  echo "repo_purity: OK"
else
  echo "repo_purity: FAIL (run: openteam workspace migrate --from-repo)" >&2
  missing=1
fi

# Policy checks (best-effort; no remote writes).
if python3 "$ROOT/scripts/policy_check.py" --quiet >/dev/null 2>&1; then
  echo "policy: OK"
else
  echo "policy: FAIL (run: ./scripts/openteam.sh policy-check)" >&2
  missing=1
fi

# Workspace checks (project truth sources must live OUTSIDE this repo).
root="$(openteam_root)"
if [[ -x "$root/openteam" ]]; then
  if "$root/openteam" workspace doctor >/dev/null 2>&1; then
    echo "workspace: OK"
  else
    echo "workspace: FAIL (run: openteam workspace init)" >&2
    missing=1
  fi
fi

echo
if [[ "$missing" -eq 1 ]]; then
  echo "doctor: FAILED" >&2
  exit 1
fi
echo "doctor: OK"
