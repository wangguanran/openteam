# TEAMOS-0012 - 01 Plan

- 标题：TEAMOS-PROJECTS-SYNC
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 以现有 Task ID 作为稳定 key（写入 Projects 自定义字段 `Task ID`）实现幂等 upsert；在此基础上补齐治理与字段：
- 写入闸门：`/v1/panel/github/sync` 在 `dry_run=false` 时强制 leader-only。
- 字段扩展：mapping.yaml 增加 Repo Locator / Repo Mode；sync 时写入（来源 task ledger repo 元信息）。

## 拆分与里程碑

- M1: panel sync leader-only
- M2: mapping.yaml 字段扩展 + sync 写入
- M3: 回归：`python3 -m unittest -q`、`./teamos doctor`

## 风险评估与闸门

- 风险等级：R2
- 审批点：
  - 无（不执行远端写入）

## 依赖

- GitHub Projects v2 绑定与 token（仅在 real sync 时需要；本任务本地回归不触发）

## 验收标准

- `dry_run=false` 时 panel sync 必须 leader-only（非 leader 返回 409）
- `mode=full` 可创建/识别新增字段并写入 Repo Locator/Mode
