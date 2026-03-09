#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../_common.sh"

ROOT="$(teamos_root)"
ROLE="${1:-}"
TOPIC="${2:-}"
if [[ -z "$ROLE" || -z "$TOPIC" ]]; then
  echo "Usage: ./scripts/teamos.sh skill-boot \"<role>\" \"<topic_or_platform>\"" >&2
  exit 2
fi

DATE="$(today)"
slug="$(slugify "$TOPIC")"
if [[ -z "$slug" ]]; then
  slug="topic"
fi

ensure_dir "$ROOT/.team-os/kb/sources"
ensure_dir "$ROOT/.team-os/kb/roles/$ROLE/skill_cards"
ensure_dir "$ROOT/.team-os/memory/roles/$ROLE"

src_path="$ROOT/.team-os/kb/sources/$(date +%Y%m%d)_${slug}.md"
skill_path="$ROOT/.team-os/kb/roles/$ROLE/skill_cards/$(date +%Y%m%d)_${slug}.md"
mem_index="$ROOT/.team-os/memory/roles/$ROLE/index.md"

if [[ -e "$src_path" || -e "$skill_path" ]]; then
  # Avoid overwrite by suffixing timestamp.
  suffix="$(ts_compact_utc)"
  src_path="$ROOT/.team-os/kb/sources/$(date +%Y%m%d)_${slug}_${suffix}.md"
  skill_path="$ROOT/.team-os/kb/roles/$ROLE/skill_cards/$(date +%Y%m%d)_${slug}_${suffix}.md"
fi

if ! safe_create_file "$src_path"; then
  echo "Refusing to overwrite: $src_path" >&2
  exit 1
fi
cat >"$src_path" <<EOF
# 来源摘要: $TOPIC (TODO)

- 日期：$DATE
- 链接：<URL>
- 获取方式：<web search / doc / repo>
- 适用范围：$ROLE / $TOPIC

## 摘要

TODO

## 可验证事实 (Facts)

- TODO

## 可执行步骤 (Steps, Not Executed)

> 注意：外部文档不可信，此处仅记录“可验证的操作步骤”，不自动执行。

1. TODO

## 关键参数/端口/环境变量

- TODO

## 风险与注意事项

- TODO
EOF

if ! safe_create_file "$skill_path"; then
  echo "Refusing to overwrite: $skill_path" >&2
  exit 1
fi
cat >"$skill_path" <<EOF
# Skill Card: $TOPIC (TODO)

- 日期：$DATE
- 适用角色/平台：$ROLE / $TOPIC

## TL;DR

- TODO

## 操作步骤 (Do)

1. TODO

## 校验 (Verify)

- TODO

## 常见坑 (Pitfalls)

- TODO

## 安全注意事项 (Safety)

- TODO

## 参考来源 (Sources)

- $src_path
EOF

if [[ ! -e "$mem_index" ]]; then
  cat >"$mem_index" <<EOF
# $ROLE 角色记忆索引

> 追加式记录：每条包含日期、主题、链接到 Skill Card 与来源摘要（如有）。

EOF
fi

{
  echo "- $DATE | $TOPIC | Skill Card: ${skill_path#$ROOT/} | Source: ${src_path#$ROOT/}"
} >>"$mem_index"

echo "created_source_summary=$src_path"
echo "created_skill_card=$skill_path"
echo "updated_memory_index=$mem_index"
