# TEAMOS-0033 - 01 Plan

- 标题：TEAMOS-CREWAI-UNIFY-PROCESS
- 日期：2026-02-26
- 当前状态：plan

## 方案概述

- 将运行态流程入口统一到 CrewAI Flow（`/v1/runs/start` 以 `flow` 为主，兼容旧 `pipeline`）。
- 保持“写真相源必须走 deterministic pipelines”的硬规则不变。
- 将健康检查主判定从 `workflows_dir_exists` 调整为 `crewai_orchestrator_exists`。
- 统一文档口径：README/TEAMOS/RUNBOOK 明确 CrewAI 为编排主引擎。

## 拆分与里程碑

- 里程碑 A：更新 `crewai_orchestrator.py` Flow->Pipeline 映射与执行回传。
- 里程碑 B：更新 `main.py` 的 run 入参模型、health 判定、task 元数据。
- 里程碑 C：更新 `task_create.py` 的任务台账编排字段。
- 里程碑 D：更新文档并通过测试。

## 风险评估与闸门

- 风险等级：R1
- 审批点：无新增高风险动作（无公网暴露、无生产写入、无敏感信息处理）。

## 依赖

- 现有 deterministic pipelines（`doctor.py`, `db_migrate.py`）
- 现有 task ledger schema（允许 additionalProperties）

## 验收标准

- `/v1/runs/start` 支持 `flow`，并兼容旧 `pipeline`。
- health check 以 CrewAI orchestrator 存在为主条件。
- 新建任务台账包含 `orchestration.engine=crewai`。
- `python3 -m unittest -q` 全量通过。
