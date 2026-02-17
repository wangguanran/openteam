# TEAMOS-0001 - 04 Test

- 标题：TEAMOS-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 文档变更 + policy 闸门（AGENTS/GOVERNANCE/RUNBOOK 关键短语强制）
- 回归：unittest
- 自检：doctor（含 repo purity / control plane / OAuth / gh / workspace）

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
TEAMOS_SELF_IMPROVE_DISABLE=1 ./teamos policy check   # PASS
TEAMOS_SELF_IMPROVE_DISABLE=1 python3 -m unittest -q  # PASS
TEAMOS_SELF_IMPROVE_DISABLE=1 ./teamos doctor         # PASS
```

## 证据

- 日志/截图/报告路径：
  - `.team-os/logs/tasks/TEAMOS-0001/04_test.md`
