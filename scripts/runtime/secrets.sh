#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../_common.sh"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/openteam.sh runtime-secrets [--path <dir>]

Default:
  --path ~/.openteam/runtime-config/default

Behavior:
  - Ensures <dir>/.env exists (creates from .env.example if missing)
  - Generates and fills ONLY missing/empty keys:
      POSTGRES_PASSWORD (hex-64)
      OH_SECRET_KEY     (urlsafe)
      PASSWORD          (urlsafe)
  - Creates a .env.bak.<timestamp> backup before editing
  - Does NOT print secret values
EOF
}

ROOT="$(openteam_root)"

target=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --path)
      target="${2:-}"
      shift 2
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

env_example="$target/.env.example"
env_file="$target/.env"

if [[ ! -f "$env_example" ]]; then
  echo "Missing: $env_example" >&2
  echo "hint: run: cd \"$ROOT\" && ./scripts/openteam.sh runtime-init --path \"$target\"" >&2
  exit 1
fi

if [[ ! -f "$env_file" ]]; then
  cp -p "$env_example" "$env_file"
fi

bak="$env_file.bak.$(ts_compact_utc)"
cp -p "$env_file" "$bak"

# Use a path-aware inline python to avoid leaking secrets to stdout.
python3 - <<PY
import secrets
from pathlib import Path

env_file = Path(r"$env_file")
lines = env_file.read_text(encoding="utf-8").splitlines()

def gen_hex64():
  return secrets.token_hex(32)

def gen_urlsafe():
  return secrets.token_urlsafe(48)

generators = {
  "POSTGRES_PASSWORD": gen_hex64,
  "OH_SECRET_KEY": gen_urlsafe,
  "PASSWORD": gen_urlsafe,
}

present = {k: False for k in generators}
changed = []

for i, line in enumerate(lines):
  if not line or line.lstrip().startswith("#") or "=" not in line:
    continue
  k, v = line.split("=", 1)
  k = k.strip()
  if k in generators:
    present[k] = True
    if v.strip() == "":
      lines[i] = f"{k}={generators[k]()}"
      changed.append(k)

if any(not v for v in present.values()):
  if lines and lines[-1].strip() != "":
    lines.append("")
  lines.append("# --- Local/OpenHands secrets (auto-generated; do not commit) ---")
  for k, ok in present.items():
    if not ok:
      lines.append(f"{k}={generators[k]()}")
      changed.append(k)

env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

# Print only metadata, never secret values.
print("updated_env=" + str(env_file))
print("updated_keys=" + ",".join(changed) if changed else "updated_keys=<none>")
PY

echo "backup=$bak"
