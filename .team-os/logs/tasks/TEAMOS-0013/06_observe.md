# TEAMOS-0013 - 06 Observe

- 标题：TEAMOS-VERIFY-0001
- 日期：2026-02-17
- 当前状态：observe

## 观测指标与口径

- `teamos doctor` 结果：必须 PASS（含 repo purity/workspace/codex oauth/gh/db/daemon）
- `requirements_raw_first.py verify`：必须 `ok=true`（无 drift/conflict）
- `prompt diff`：必须 clean（决定性）
- `self_improve_daemon`：必须 running=true；DB 中 `self_improve_runs.count>=1`（当 TEAMOS_DB_URL 配置）
- `approvals`：DB-backed list 可读；至少包含 1 条 APPROVED 与 1 条 DENIED 记录（验证策略与落库）

## 结果

- doctor: PASS（含 DB=OK, daemon running=true）
- requirements verify: PASS（pipeline）
- prompt diff: PASS（clean）
- self-improve: PASS（applied_count=3，DB 记录 ok）
- approvals: PASS（DB 可读，包含 APPROVED/DENIED）

## 结论

- 是否达标：
- 达标（满足本轮 verify 与审计闭环）
- 是否需要后续任务：
  - 建议后续：runtime upgrade 流程将 template 变更同步到运行态（避免 control-plane 旧版本导致的 verify/rebuild 结果不一致）
