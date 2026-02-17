# TEAMOS-0002 - 04 Test

- 标题：TEAMOS-ALWAYS-ON-SELF-IMPROVE
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- self-improve daemon pipeline（run-once + daemon start/status/stop）
- requirements_raw_first pipeline（self-improve 写入路径）
- 回归：unittest + doctor + policy check

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
./teamos policy check           # PASS
python3 -m unittest -q          # PASS
./teamos doctor                 # PASS

# Self-improve (>=3 proposals; updates requirements; panel sync dry-run)
./teamos self-improve --force   # PASS (proposal md + requirements updated)

# Daemon lifecycle
./teamos daemon start           # PASS (pid/log/state)
./teamos daemon status          # PASS (running=true)
./teamos daemon stop            # optional
```

## 证据

- 日志/截图/报告路径：
  - `.team-os/ledger/self_improve/20260217T005213Z-proposal.md`
  - `.team-os/state/self_improve_state.json` (gitignored)
