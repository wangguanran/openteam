# TEAMOS-0020 - 01 Plan

- 标题：Git workflow: no per-task branches + cleanup merged temp branches
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 分两步完成：
  1) 规则变更：更新文档 + 调整 `task ship` 与 approvals 的 task_id 关联逻辑，支持无分支工作流（默认在 `main` 上 ship）。
  2) 分支清理：仅删除“已合并到 `origin/main`”的 `origin/teamos/*` 分支与本地同名分支；删除前通过 approvals 记录审计。

## 拆分与里程碑

- M1：更新 `task ship`：不强制 `teamos/<TASK_ID>-...`；head==base 时跳过 PR。
- M2：更新 approvals 的 task_id 关联：优先读取 `TEAMOS_TASK_ID` env。
- M3：更新治理文档：Git 纪律不再要求每任务一分支。
- M4：获取审批后执行分支清理（remote + local），并记录证据。
- M5：`unittest`/`policy check`/`doctor` → `task close` → `task ship`。

## 风险评估与闸门

- 风险等级：R2
- 审批点：
  - 删除 remote 分支（HIGH）：必须走 `./teamos approvals` 记录审计（单机需人工确认 YES）。

## 依赖

- `gh` 已登录（用于确认无 open PR；本任务清理使用 git push --delete）

## 验收标准

- 文档已更新：不再要求每任务一分支
- `./teamos task ship <TASK_ID>` 在 `main` 上可用（不创建 PR）
- approvals task_id 关联：无分支时可通过 `TEAMOS_TASK_ID` 关联
- `origin/teamos/*`（已合并分支）与本地 `teamos/*` 分支已清理
- `python3 -m unittest -q`、`./teamos policy check`、`./teamos doctor` 通过
