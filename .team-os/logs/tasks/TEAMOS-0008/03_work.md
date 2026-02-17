# TEAMOS-0008 - 03 Work

- 标题：TEAMOS-APPROVALS-DB
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 新增 Postgres schema migrations：`.team-os/db/migrations/0001_init.sql`
  - 新增 DB 迁移 runner：`.team-os/scripts/pipelines/db_migrate.py`（含 SQL splitter）
  - 新增审批引擎：`.team-os/scripts/pipelines/approvals.py` + `.team-os/policies/approvals.yaml`
  - doctor 增强：`.team-os/scripts/pipelines/doctor.py` 增加 DB 检查（`TEAMOS_DB_URL` 未设置时 SKIP）
  - CLI 强制闸门：`teamos` 高风险执行点调用 approvals pipeline
    - `workspace migrate --force`
    - `node add --execute`
    - `repo create --approve`
  - 新增回归测试：SQL splitter + risk classifier
- 关键命令（含输出摘要）：
  - `python3 -m unittest -q`：PASS
  - `./teamos doctor`：PASS（db: SKIP TEAMOS_DB_URL not set）
  - `./teamos --help`：新增 `db`/`approvals` 命令可见
- 决策与理由：
  - risk classifier 对未知 action_kind 默认 HIGH（fail-safe），避免漏拦截。
  - 未配置 `TEAMOS_DB_URL` 时允许运行：审批写入 Workspace fallback 审计文件，并标记 pending_sync。

## 变更文件清单

- `.team-os/db/migrations/0001_init.sql`
- `.team-os/policies/approvals.yaml`
- `.team-os/scripts/pipelines/_db.py`
- `.team-os/scripts/pipelines/db_migrate.py`
- `.team-os/scripts/pipelines/approvals.py`
- `.team-os/scripts/pipelines/doctor.py`
- `teamos`
- `tests/test_db_migrate_splitter.py`
- `tests/test_risk_classifier.py`
