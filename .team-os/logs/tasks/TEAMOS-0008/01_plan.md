# TEAMOS-0008 - 01 Plan

- 标题：TEAMOS-APPROVALS-DB
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 以 Python pipelines 为唯一执行路径实现 DB 迁移与审批：
- `db_migrate.py`：按版本顺序执行 `.team-os/db/migrations/*.sql`，写入 `schema_migrations`。
- `approvals.py`：risk_classify（确定性）→ request/decide/list（DB 优先；无 DB 回退 Workspace `shared/audit/approvals.jsonl`）。
- CLI：高风险执行点在执行前调用 approvals pipeline；未 APPROVED 直接中止。
- doctor：当 `TEAMOS_DB_URL` 配置后必须验证驱动/连通性/迁移状态；未配置则 SKIP。

## 拆分与里程碑

- M1: v1 Postgres schema migration（含 runtime 表 + cluster/approvals 表）
- M2: DB migration runner + unit test（SQL splitter）
- M3: approvals engine + policy + unit test（risk classifier）
- M4: CLI 闸门接入 + doctor DB check
- M5: 回归：`python3 -m unittest -q`、`./teamos doctor`

## 风险评估与闸门

- 风险等级：R2
- 审批点：
  - 无（本任务本身不执行高风险动作；只实现闸门机制）

## 依赖

- 可选依赖：`psycopg`（当配置 `TEAMOS_DB_URL` 且需要 DB 功能时）

## 验收标准

- `./teamos doctor`：DB 未配置时 PASS（DB=SKIP）；配置 `TEAMOS_DB_URL` 时能检测驱动/连通性/迁移状态。
- `./teamos db migrate --dry-run` 可列出 migrations；配置 DB 时可执行迁移。
- `./teamos approvals list` 可运行（DB 或 fallback）。
- 高风险执行点必须先走 approvals：
  - `./teamos workspace migrate --from-repo --force` 执行前请求审批
  - `./teamos node add ... --execute` 执行前请求审批
  - `./teamos repo create ... --approve` 执行前请求审批
