# TEAMOS-0007 - 01 Plan

- 标题：TEAMOS-AUDIT-0001
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 使用决定性 Python pipeline 生成“执行策略审计”报告：
  - 执行只读命令（doctor/policy/unittest/daemon status）并收集尾部证据（tails）。
  - 静态检查关键能力是否存在（required pipelines/policies/DB/migrations/approvals/cluster_election/recovery）。
  - 输出 PASS/FAIL/WAIVED 列表，并附带原因与缺口定位线索（missing paths）。
  - 报告落盘到 `docs/audits/EXECUTION_STRATEGY_AUDIT_<ts>.md`。

## 拆分与里程碑

- M1：新增 pipeline `.team-os/scripts/pipelines/audit_execution_strategy.py`
- M2：CLI 增加 `./teamos audit execution-strategy`
- M3：生成审计报告并在任务日志记录证据

## 风险评估与闸门

- 风险等级：R1
- 审批点：无
- 闸门：
  - `python3 -m unittest -q`
  - `./teamos policy check`
  - `./teamos doctor`
  - `./teamos task close TEAMOS-0007 --scope teamos`

## 依赖

- 无新增依赖（stdlib + 既有 teamos 工具链）

## 验收标准

- `./teamos audit execution-strategy` 可生成审计报告（确定性输出格式，包含 PASS/FAIL/WAIVED）
- `docs/audits/EXECUTION_STRATEGY_AUDIT_<ts>.md` 已落盘并可追溯
