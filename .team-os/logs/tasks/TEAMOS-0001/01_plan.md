# TEAMOS-0001 - 01 Plan

- 标题：TEAMOS-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 更新三份核心文档以统一治理口径，并将关键流程“决定性化”到 `policy check`，从而在 `task close` 闸门时强制执行。

## 拆分与里程碑

- 里程碑：
  - M1：更新 `AGENTS.md`（统一任务流程/脚本优先/真相源禁止手改/Git 纪律/验收清单）
  - M2：同步 `docs/GOVERNANCE.md`（Update Unit + Git 纪律 + 决定性产物策略）
  - M3：同步 `docs/EXECUTION_RUNBOOK.md`（将 `./teamos` 作为主入口；任务创建/关闭命令）
  - M4：加强 `.team-os/scripts/policy_check.py`（验证关键文档包含 canonical task workflow）
  - M5：运行 `./teamos task close TEAMOS-0001 --scope teamos` 通过并提交推送

## 风险评估与闸门

- 风险等级：R?
- 审批点：
  - ...
  - 风险等级：R1（仅文档与本地 policy 校验）
  - 审批点：无

## 依赖

- 无外部依赖（不需要联网检索）。

## 验收标准

- `AGENTS.md` 明确包含：
  - `./teamos task new --scope teamos`
  - `./teamos task close`
  - “脚本优先/禁止 Agent 直写真相源/commit+push 纪律/Repo vs Workspace 边界”
- `docs/GOVERNANCE.md`、`docs/EXECUTION_RUNBOOK.md` 与 `AGENTS.md` 口径一致
- `./teamos policy check`：PASS
- `python3 -m unittest -q`：PASS
