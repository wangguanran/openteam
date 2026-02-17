# TEAMOS-0009 - 01 Plan

- 标题：TEAMOS-CENTRAL-MODEL-ALLOWLIST
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 以策略 allowlist 作为唯一真相源，提供双路径校验：
- Control Plane：`cluster_manager.attempt_elect` 在 cluster enabled 时先做资格校验，失败则拒绝竞选并返回原因（同时由 runtime DB 事件记录）。
- CLI/Pipeline：`cluster_election.py qualify` 提供离线可重复校验（用于运维/节点自检）。

## 拆分与里程碑

- M1: allowlist 策略文件落盘
- M2: pipeline `cluster_election.py qualify`
- M3: Control Plane 集群选主闸门接入 + cluster status 输出资格信息
- M4: CLI `cluster qualify` + `cluster status` 输出增强
- M5: 回归测试 + `python3 -m unittest -q`

## 风险评估与闸门

- 风险等级：R2
- 审批点：
  - 无（不执行高风险动作）

## 依赖

- 依赖环境变量（cluster enabled 时必需）：`TEAMOS_LLM_MODEL_ID`

## 验收标准

- `python3 -m unittest -q` PASS
- `teamos cluster qualify` 在未设置 `TEAMOS_LLM_MODEL_ID` 时返回 non-zero（fail-safe）
- Control Plane `/v1/cluster/status` 返回 `llm_profile` 与 `leader_qualification`
- Control Plane `/v1/cluster/elect/attempt` 在 cluster enabled 时若不满足 allowlist 则拒绝竞选并返回原因
