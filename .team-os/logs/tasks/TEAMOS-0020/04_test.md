# TEAMOS-0020 - 04 Test

- 标题：Git workflow: no per-task branches + cleanup merged temp branches
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 单元测试：`python3 -m unittest -q`
- 合规闸门：`./teamos policy check`、`./teamos doctor`
- 任务 DoD：`./teamos task close TEAMOS-0020`

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）

python3 -m unittest -q
# PASS

./teamos policy check
# PASS

./teamos doctor
# PASS (db SKIP when TEAMOS_DB_URL unset)
```

## 证据

- 日志/截图/报告路径：
