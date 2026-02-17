# TEAMOS-0010 - 01 Plan

- 标题：TEAMOS-RECOVERY
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 在 Control Plane 内实现确定性的恢复扫描与恢复逻辑：
- 扫描：基于 ledger 汇总 active tasks，并计算 gates：
  - NEED_PM_DECISION：task ledger `need_pm_decision=true`
  - WAITING_APPROVAL：若 `TEAMOS_DB_URL` 为 postgres，查询 approvals 表是否存在 `status=REQUESTED` 的记录
  - BLOCKED：task status=blocked
- 恢复：仅对无 gates 的任务执行恢复（upsert run + Process-Guardian agent），并返回 resumed/skipped。
- 启动自恢复：startup 后台线程执行一次 scan + resume（best-effort）。

## 拆分与里程碑

- M1: recovery scan 输出 gates + snapshot markdown
- M2: recovery resume gate-aware + leader-only
- M3: startup auto-run（可通过 `TEAMOS_RECOVERY_AUTO=0` 关闭）
- M4: 回归：`python3 -m unittest -q`、`./teamos doctor`

## 风险评估与闸门

- 风险等级：R2
- 审批点：
  - 无（不执行高风险动作）

## 依赖

- 可选：Postgres approvals 表（用于 WAITING_APPROVAL gate）

## 验收标准

- `/v1/recovery/scan` 返回 active_tasks[*].gates 且 snapshot 写入 `.team-os/cluster/state/`
- `/v1/recovery/resume` 返回 resumed 与 skipped（含 gates）
- startup 后自动触发一次 scan + resume（best-effort，不影响服务启动）
