# TEAMOS-0018 - 01 Plan

- 标题：Docs: Raw v3 + Self-Improve separation + Locks
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 目标：更新 Team-OS 文档，使其与当前实现保持一致并可执行：
  - 需求处理协议 v3：Raw-only（用户原文）+ 可行性评估 + 旁路索引 `raw_assessments.jsonl`
  - Self-Improve 与 Raw 分离：proposal 独立文件 + system channel 更新 Expanded
  - 并发锁：repo lock + scope lock；`LOCK_BUSY` 行为与排障
  - 高风险审批：集群 Brain 自动审批 vs 单机人工确认（记录落 DB/审计文件）

## 拆分与里程碑

- 更新文件：
  - `AGENTS.md`
  - `docs/EXECUTION_RUNBOOK.md`
  - `docs/GOVERNANCE.md`
- 回归闸门：
  - `./teamos task close TEAMOS-0018` 通过（含 policy_check + purity + tests）

## 风险评估与闸门

- 风险等级：R1（文档更新，不涉及高风险动作）
- 审批点：无

## 依赖

- 无

## 验收标准

- 文档包含可执行命令且不声明不存在的子命令
- `./teamos policy check`：PASS
- `python3 -m unittest -q`：PASS
