# TEAMOS-0003 - 01 Plan

- 标题：TEAMOS-GIT-PUSH-DISCIPLINE
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 通过新增决定性 `task_ship` pipeline，将 close→闸门→commit→push 串成一个可重复命令，并在失败场景将任务标记 BLOCKED（落盘原因与修复步骤）。

## 拆分与里程碑

- M1：补齐 task ledger schema（支持 blocked）
- M2：实现 `.team-os/scripts/pipelines/task_ship.py`
  - 调用 `task_close` 作为前置闸门
  - secrets scan（内容模式 + 关键前缀）
  - push 预检（dry-run）与失败 BLOCKED 处理
  - 可选 PR 创建（gh）
- M3：CLI 接入 `./teamos task ship`
- M4：更新文档与治理口径（AGENTS/GOVERNANCE/RUNBOOK）
- M5：dogfood：用 `./teamos task ship TEAMOS-0003` 完成本任务的 commit+push

## 风险评估与闸门

- 风险等级：R?
- 审批点：
  - ...
  - 风险等级：R1
  - 审批点：无（不强推；push/PR 属于常规工程动作）

## 依赖

- `git`（本地）
- 可选：`gh`（PR 自动创建）

## 验收标准

- `./teamos task ship <TASK_ID>` 存在并可用
- close 失败时：ship 不产生 commit/push
- secrets scan 命中时：ship 阻断并标记任务 `blocked`
- origin 缺失/无权限时：仍可 commit，任务标记 `blocked`，并输出修复步骤
