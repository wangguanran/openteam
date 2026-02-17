# TEAMOS-0010 - 00 Intake

- 标题：TEAMOS-RECOVERY
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 实现断点续跑：Control Plane 启动后自动扫描未完成任务并恢复（停在审批/PM 决策闸门）。

## 目标/非目标

- 目标：
- 强化 `/v1/recovery/scan`：输出每个 active task 的 gates（NEED_PM_DECISION / WAITING_APPROVAL / BLOCKED）。
- 强化 `/v1/recovery/resume`：仅恢复无 gates 的任务；其余跳过并返回原因；leader-only 写入。
- Startup 自动执行一次 recovery scan + resume（best-effort，不崩溃）。
- 非目标：
- 本任务不实现真正的任务执行器/分布式调度（仍为占位恢复：run + Process-Guardian agent）。
- 本任务不实现 DB-first lease 接管序列（另起任务）。

## 约束与闸门

- 风险等级：R2（集群/恢复关键路径变更）
- 需要审批的动作（如有）：无（不执行高风险动作）

## 澄清问题 (必须回答)

- Q: 未配置 Postgres 时如何判断 WAITING_APPROVAL？A: 仅在 `TEAMOS_DB_URL` 为 postgres 且 approvals 表可查询时启用；否则跳过该 gate（不影响 NEED_PM_DECISION/BLOCKED）。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/templates/runtime/orchestrator/app/main.py`（recovery scan/resume + startup auto-run）
