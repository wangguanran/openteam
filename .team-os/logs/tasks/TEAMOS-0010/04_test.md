# TEAMOS-0010 - 04 Test

- 标题：TEAMOS-RECOVERY
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 自检：
  - `python3 -m unittest -q`
  - `./teamos doctor`
- API（手动/运行态验证）：
  - `/v1/recovery/scan` 输出 gates + snapshot
  - `/v1/recovery/resume` gate-aware 恢复

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
python3 -m unittest -q
# OK

./teamos doctor
# PASS
```

## 证据

- 日志/截图/报告路径：
  - `.team-os/logs/tasks/TEAMOS-0010/04_test.md`
