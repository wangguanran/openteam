# TEAMOS-0012 - 04 Test

- 标题：TEAMOS-PROJECTS-SYNC
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 自检：
  - `python3 -m unittest -q`
  - `./teamos doctor`
- API（运行态/手动）：
  - `/v1/panel/github/sync` 非 dry-run 必须 leader-only

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
  - `.team-os/logs/tasks/TEAMOS-0012/04_test.md`
