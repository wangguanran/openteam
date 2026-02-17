# TEAMOS-0013 - 03 Work

- 标题：TEAMOS-VERIFY-0001
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 验证 Postgres DB（TEAMOS_DB_URL）可用：创建 `team_os` DB、执行 migrations（0001），doctor 可检测到 migrations。
  - 审批引擎（approvals）在 DB 中落盘：演示 leader policy always-deny 与 single manual-required 的记录与决策写入。
  - Always-On Self-Improve：强制 run-once 产出 >=3 proposals、写入 Team‑OS requirements，并写入 `self_improve_runs` 表。
  - 修复 requirements drift：使用确定性 pipeline 重建 `REQUIREMENTS.md`，使 `requirements_raw_first.py verify` 通过。
  - Prompt 决定性构建：`MASTER_PROMPT.md` + manifest/changelog/history 更新，`prompt diff` clean。
  - 审计脚本治理升级：更新 audit 生成器覆盖 DB/approvals/allowlist/panel sync/self-improve DB 记录等，生成最新审计报告。
- 关键命令（含输出摘要，不含 secrets）：
  - `python3 -m unittest -q` -> OK
  - `./teamos doctor`（含 DB）-> PASS
  - `./teamos db migrate` -> applied 0001
  - `python3 .team-os/scripts/pipelines/approvals.py request/decide` -> DB 记录可查
  - `./teamos self-improve --force` -> applied_count=3 + db_record.ok=true
  - `python3 .team-os/scripts/pipelines/requirements_raw_first.py rebuild/verify --scope teamos` -> verify ok=true
  - `./teamos prompt build --scope teamos` + `./teamos prompt diff --scope teamos` -> clean
  - `./teamos panel sync --project teamos --full --dry-run` -> action plan generated
  - `./teamos audit execution-strategy` + `./teamos audit deterministic-gov` -> PASS
- 决策与理由：
  - requirements drift 以 pipeline 为真相源：控制面 runtime 与 template/pipeline 存在版本差异时，优先以 repo 内 deterministic pipeline 渲染结果为准，并据此重建。

## 变更文件清单

- `.team-os/scripts/pipelines/audit_execution_strategy.py`
- `.team-os/scripts/pipelines/audit_deterministic_gov.py`
- `docs/audits/EXECUTION_STRATEGY_AUDIT_20260217T044007Z.md`
- `docs/audits/DETERMINISTIC_GOV_AUDIT_20260217T044405Z.md`
- `.team-os/ledger/self_improve/20260217T042520Z-proposal.md`
- `docs/teamos/requirements/raw_inputs.jsonl`
- `docs/teamos/requirements/requirements.yaml`
- `docs/teamos/requirements/REQUIREMENTS.md`
- `docs/teamos/requirements/CHANGELOG.md`
- `prompt-library/teamos/MASTER_PROMPT.md`
- `prompt-library/teamos/prompt_manifest.json`
- `prompt-library/teamos/PROMPT_CHANGELOG.md`
- `prompt-library/teamos/history/20260217T043055Z.md`
