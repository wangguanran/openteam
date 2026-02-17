# TEAMOS-0011 - 01 Plan

- 标题：TEAMOS-ALWAYS-ON
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- Always‑On 通过两层保障：
- Control Plane：startup 后台线程 best-effort 执行 `self_improve_daemon.py start`，确保 daemon 常驻。
- Daemon：每次 `run_once` 结束后，若配置 `TEAMOS_DB_URL`，则写入 `self_improve_runs`（migrations 幂等）。
- doctor：输出 daemon.running 与 pid（用于合规验收与排障）。

## 拆分与里程碑

- M1: Control Plane startup ensure daemon
- M2: daemon DB 记录
- M3: doctor 输出增强
- M4: 回归：`python3 -m unittest -q`、`./teamos doctor`

## 风险评估与闸门

- 风险等级：R2
- 审批点：
  - 无（不执行高风险动作）

## 依赖

- 可选：Postgres + `psycopg`（用于 `self_improve_runs` 写入）

## 验收标准

- Control Plane 启动后 daemon 能自动处于 running（或在 status/doctor 可见原因）
- `self_improve_daemon.py run-once` 返回 `db_record.ok=true`（当 DB 可用）
- `./teamos doctor` 输出 `self_improve_daemon.running=...`
