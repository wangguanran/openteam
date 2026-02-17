# TEAMOS-0004 - 01 Plan

- 标题：DETERMINISTIC-GOV-AUDIT
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 用决定性脚本运行一组核心闸门（doctor/policy/unittest/requirements verify/prompt compile dry-run/daemon status），并将结果与任务证据（branch/commit/PR）汇总成可追溯审计报告。

## 拆分与里程碑

- M1：实现审计脚本 `.team-os/scripts/pipelines/audit_deterministic_gov.py`
- M2：运行脚本生成 `docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md`
- M3：`./teamos task close TEAMOS-0004` 通过并提交推送

## 风险评估与闸门

- 风险等级：R?
- 审批点：
  - ...
  - 风险等级：R1
  - 审批点：无

## 依赖

- `gh`（可选，用于获取 PR URL；缺失时报告仍可生成但 PR 字段为 n/a）

## 验收标准

- 生成审计报告文件：`docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md`
- 报告包含 tasks/commit/PR 引用与核心闸门结果
