# TEAMOS-0017 - 01 Plan

- 标题：Hotfix: restore task creation pipeline
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 背景：在 `TEAMOS-0016` 分支上，`./teamos task new` 触发 `.team-os/scripts/pipelines/task_create.py` 的 `SyntaxError`，导致“统一任务流程”不可用。
- 目标：修复 `task_create.py`，并按并发治理要求接入 `locks.py`（repo lock + scope lock + `atexit` 清理），恢复可创建任务的能力。

## 拆分与里程碑

- 里程碑：
  - `task_create.py` 可 `py_compile`
  - `./teamos task new --scope teamos ...` 可成功执行并生成 ledger/logs/metrics
  - `python3 -m unittest -q` 通过

## 风险评估与闸门

- 风险等级：R1（本地脚本修复；不涉及删除数据/强推/开放端口/生产发布）
- 审批点：无

## 依赖

- 无

## 验收标准

- `python3 -m py_compile .team-os/scripts/pipelines/task_create.py` 通过
- `./teamos task new --scope teamos --title "<...>"` 可用
- `./teamos task close TEAMOS-0017` 通过（含 policy_check + repo purity + tests）
