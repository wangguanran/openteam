# TEAMOS-0019 - 03 Work

- 标题：Verify: Req v3 + locks + approvals DB
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
- 新增确定性审计脚本：`.team-os/scripts/pipelines/audit_reqv3_locks.py`（端到端验证 REQv3 + Locks + Approvals(DB) + Self-Improve separation）。
- CLI 接入审计：`./teamos audit reqv3-locks`。
- 修复锁降级策略：DB 锁争用时不降级为 file lock；仅 DB 不可用时才降级（避免 DB 与 file lock 同时存在导致竞态）。
- 稳定回归测试：`evals/test_concurrency_locks.py` 显式在 file-lock 单测里清空 `TEAMOS_DB_URL`，避免 CI/本机启用 DB 时走错 backend。
- 修复 Self-Improve 写入通道：从 raw-first 改为 system channel（不写 `raw_inputs.jsonl`），并修复 changelog `raw=` 引用为 `SYSTEM`。
- 关键命令（含输出摘要）：
  - `./teamos audit reqv3-locks` → 生成 PASS 报告：`docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md`
  - `python3 -m unittest -q` → PASS（含 `evals/test_concurrency_locks.py`）
  - `./teamos workspace init` → 修复审计用测试项目目录缺失（Workspace-only；不写 repo）
  - `./teamos doctor` → PASS（workspace OK / repo purity OK / control-plane OK）
  - `./teamos policy check` → PASS
  - （审计内）`./teamos db migrate` → ok=true skipped=["0001"]
  - （审计内）`./teamos approvals list --limit 5` → ok（可读）
  - （审计内）`python .../.team-os/scripts/pipelines/approvals.py ... request --yes` → 写入 DB 成功（仅记录，不做真实 repo 创建）
- 决策与理由：
  - 审计脚本采用“真实执行路径调用 pipelines”，避免手工拼接伪结果；输出报告作为可追溯证据。
  - 锁策略明确区分“DB 不可用”与“DB 锁争用”，防止并发写入被错误降级为 file lock。

## 变更文件清单

- `.team-os/scripts/pipelines/audit_reqv3_locks.py`
- `teamos`（新增子命令：`audit reqv3-locks`）
- `.team-os/scripts/pipelines/locks.py`
- `evals/test_concurrency_locks.py`
- `.team-os/scripts/pipelines/self_improve_daemon.py`
- `.team-os/scripts/pipelines/requirements_raw_first.py`
- `.team-os/templates/runtime/orchestrator/app/requirements_store.py`
- `docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md`
- `.team-os/ledger/self_improve/20260217T091621Z-proposal.md`
