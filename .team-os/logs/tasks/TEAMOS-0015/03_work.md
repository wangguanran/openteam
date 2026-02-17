# TEAMOS-0015 - 03 Work

- 标题：TEAMOS-SELF-IMPROVE-SEPARATION
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 新增 system update channel：`add_requirement_system_update`（不写 raw_inputs、不写 feasibility/raw_assessments）。
  - 新增 pipeline：`.team-os/scripts/pipelines/system_requirements_update.py`（可脚本化更新 Expanded）。
  - Self-Improve runner 改造：不再调用 `add_requirement_raw_first`，改用 system update channel，source 固定为 `SYSTEM_SELF_IMPROVE`。
  - 新增回归测试：system update 不污染 raw/feasibility。

- 关键命令（含输出摘要）：
  - `python3 -m unittest -q` -> OK

- 决策与理由：
  - Self-Improve 产物必须与 Raw 完全隔离：Raw 仅用户原文；系统生成需求通过独立 channel 写入 Expanded，并显式标记 source。

## 变更文件清单

- `.team-os/templates/runtime/orchestrator/app/requirements_store.py`
- `.team-os/templates/runtime/orchestrator/app/self_improve_runner.py`
- `.team-os/scripts/pipelines/system_requirements_update.py`
- `evals/test_system_requirements_update.py`
