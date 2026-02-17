# TEAMOS-0014 - 03 Work

- 标题：TEAMOS-RAW-FEASIBILITY-V3
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - Raw-First v3：`raw_inputs.jsonl` 仅允许用户原文 + 最小元数据（raw_id/timestamp/scope/user/channel/text/text_sha256），禁止 system/self-improve 写入。
  - 新增确定性可行性评估（Feasibility Assessment）：每条 raw 生成 `feasibility/<raw_id>.md` + 旁路索引 `raw_assessments.jsonl`（append-only）。
  - requirements pipeline：移除把 workstream 注入 raw 文本的行为，workstream 改为独立 hint 字段传递。
  - 控制面事件：从 `raw_input_timestamp` 补充升级为 `raw_id` 主键。
  - repo 现存污染修复：对 `docs/teamos/requirements/raw_inputs.jsonl` 做一次性迁移（归档 legacy），清空为 user-only v3。

- 关键命令（含输出摘要）：
  - `python3 -m unittest -q` -> OK
  - `python3 .team-os/scripts/pipelines/requirements_raw_first.py migrate-v3 --scope teamos` -> migrated=true, user_lines_kept=0

- 决策与理由：
  - Self-Improve 暂时仍走 `add_requirement_raw_first` 入口，但通过 system/source 识别跳过 raw capture，避免继续污染 raw_inputs（后续任务 B 再改为 system update channel）。

## 变更文件清单

- `.team-os/templates/runtime/orchestrator/app/requirements_store.py`
- `.team-os/templates/runtime/orchestrator/app/feasibility.py`
- `.team-os/scripts/pipelines/feasibility_assess.py`
- `.team-os/scripts/pipelines/requirements_raw_first.py`
- `.team-os/templates/runtime/orchestrator/app/main.py`
- `.team-os/schemas/requirement_raw_input.schema.json`
- `.team-os/schemas/requirement_raw_assessment.schema.json`
- `evals/test_requirements_raw_first.py`
- `docs/teamos/requirements/raw_inputs.jsonl` (+ legacy 备份)
