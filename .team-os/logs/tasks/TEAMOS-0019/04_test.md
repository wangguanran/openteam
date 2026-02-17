# TEAMOS-0019 - 04 Test

- 标题：Verify: Req v3 + locks + approvals DB
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 单元测试：`python3 -m unittest -q`（包含并发锁回归 `evals/test_concurrency_locks.py`）
- 审计脚本：`./teamos audit reqv3-locks`（生成端到端证据报告）
- 合规闸门：`./teamos policy check`、`./teamos doctor`
- 任务 DoD：`./teamos task close TEAMOS-0019`

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）

./teamos audit reqv3-locks
# PASS → docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md

python3 -m unittest -q
# PASS

./teamos policy check
# PASS

./teamos doctor
# PASS (workspace OK; repo purity OK; control-plane OK; db SKIP when TEAMOS_DB_URL unset)
```

## 证据

- 报告路径：`docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md`
