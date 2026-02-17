# TEAMOS-0013 - 04 Test

- 标题：TEAMOS-VERIFY-0001
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 任务闭环验收（teamos doctor / task close / ship gates）
- Raw‑First requirements pipeline（rebuild/verify）
- Prompt pipeline（build/diff）
- DB + migrations（doctor + db migrate）
- Approvals（request/decide/list, DB-backed）
- Central model allowlist（cluster qualify）
- Panel sync dry-run（幂等动作计划）

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
python3 -m unittest -q
# OK

./teamos doctor
# PASS (repo purity/workspace/codex/gh/control-plane/self-improve daemon)

./teamos db migrate
# applied: 0001 (idempotent)

python3 .team-os/scripts/pipelines/requirements_raw_first.py --repo-root . --workspace-root ~/.teamos/workspace rebuild --scope teamos
python3 .team-os/scripts/pipelines/requirements_raw_first.py --repo-root . --workspace-root ~/.teamos/workspace verify --scope teamos
# ok=true

./teamos prompt build --scope teamos
./teamos prompt diff --scope teamos
# prompt_diff: clean

./teamos panel sync --project teamos --full --dry-run
# 输出 action plan（无 GitHub 调用）

TEAMOS_LLM_MODEL_ID=gpt-5 ./teamos cluster qualify
# qualified=true

./teamos approvals list
# db.enabled=true + approvals 可读
```

## 证据

- 日志/截图/报告路径：
  - `docs/audits/EXECUTION_STRATEGY_AUDIT_20260217T044007Z.md`
  - `docs/audits/DETERMINISTIC_GOV_AUDIT_20260217T044405Z.md`
