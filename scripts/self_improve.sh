#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

ROOT="$(teamos_root)"
DATE="$(today)"
slug="self-improve"
ts="$(ts_compact_utc)"

ensure_dir "$ROOT/.team-os/ledger/self_improve"

out="$ROOT/.team-os/ledger/self_improve/${ts}_${slug}.md"
if [[ -e "$out" ]]; then
  echo "Refusing to overwrite: $out" >&2
  exit 1
fi

cat >"$out" <<EOF
# Self-Improve Entry

- 日期：$DATE
- 触发来源：<TASK_ID or INCIDENT_ID>
- 风险等级：R0/R1/R2/R3

## 问题

- ...

## 改进方案

- ...

## 验收标准

- ...

## 证据链接

- ...

## issue/PR

- issue:
- pr:
EOF

echo "self_improve_entry=$out"
echo "next: if you want to open an issue, run: $ROOT/scripts/open_issue.sh \"$out\""

