# TEAMOS-0004 - 04 Test

- 标题：DETERMINISTIC-GOV-AUDIT
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 审计脚本可运行并生成报告
- 回归：unittest + doctor + policy check

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
./teamos policy check                         # PASS
python3 -m unittest -q                        # PASS
./teamos doctor                               # PASS
python3 .team-os/scripts/pipelines/audit_deterministic_gov.py --repo-root . --workspace-root ~/.teamos/workspace  # PASS
./teamos audit deterministic-gov --out /tmp/teamos_audit_test2.md  # PASS
```

## 证据

- 日志/截图/报告路径：
  - `docs/audits/DETERMINISTIC_GOV_AUDIT_20260217T011242Z.md`
