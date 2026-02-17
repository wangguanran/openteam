# TEAMOS-0018 - 04 Test

- 标题：Docs: Raw v3 + Self-Improve separation + Locks
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 文档与 CLI 命令一致性（help 校验）
- policy_check 规范闸门
- 单元测试回归

## 执行记录

```bash
./teamos req add --help
./teamos approvals --help
./teamos daemon --help
# OK (help 可用)

./teamos policy check
# PASS

python3 -m unittest -q
# PASS
```

## 证据

- 日志/截图/报告路径：
  - `AGENTS.md`
  - `docs/EXECUTION_RUNBOOK.md`
  - `docs/GOVERNANCE.md`
