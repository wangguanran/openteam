# TEAMOS-0008 - 00 Intake

- 标题：TEAMOS-APPROVALS-DB
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 实现确定性的风险分类 + 审批引擎，并将高风险动作审批记录落盘到 Postgres（不可用则 Workspace 本地审计回退），同时在 CLI 中强制接入审批闸门。

## 目标/非目标

- 目标：
- 提供 `db migrate` 迁移器与 v1 SQL migrations（`.team-os/db/migrations/`），用于初始化共享中枢 Postgres schema。
- 提供 `approvals` pipeline：risk classify / request / decide / list（DB 优先，本地回退）。
- `teamos doctor` 增加 DB 连通性与迁移状态检查（`TEAMOS_DB_URL` 未设置则 SKIP）。
- CLI 高风险执行点强制走审批：`repo create --approve`、`workspace migrate --force`、`node add --execute`。
- 非目标：
- 本任务不实现 leader election/lease、模型 allowlist、断点恢复（另起任务）。
- 本任务不强制要求运行态一定配置 Postgres（但当设置 `TEAMOS_DB_URL` 时必须可用并可迁移）。

## 约束与闸门

- 风险等级：R2（高风险治理机制变更；涉及高风险动作闸门/审批记录）
- 需要审批的动作（如有）：无（实现/测试均为低风险；不执行 rm -rf/force push/公网端口/生产发布）

## 澄清问题 (必须回答)

- Q: 未配置 `TEAMOS_DB_URL` 时是否允许运行？A: 允许，doctor DB=SKIP；审批写入 Workspace 本地审计文件并标记 pending_sync。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/db/migrations/0001_init.sql`
- `.team-os/scripts/pipelines/db_migrate.py`
- `.team-os/scripts/pipelines/approvals.py`
- `.team-os/policies/approvals.yaml`
- `teamos`（CLI：新增 `db`/`approvals` 命令；高风险命令接入审批闸门）
- `.team-os/scripts/pipelines/doctor.py`（DB 检查）
- `tests/test_db_migrate_splitter.py`
- `tests/test_risk_classifier.py`
