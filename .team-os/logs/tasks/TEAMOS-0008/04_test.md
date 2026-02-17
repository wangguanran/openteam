# TEAMOS-0008 - 04 Test

- 标题：TEAMOS-APPROVALS-DB
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 单元测试（stdlib unittest）：
  - SQL splitter 行为（含 dollar quote / 注释 / 字符串分号）
  - risk classifier 对已知/未知 action_kind 的判定
- 端到端自检：
  - `./teamos doctor`（DB 未配置时应 SKIP 且整体 PASS）
  - `./teamos --help`（命令面新增 `db`/`approvals`）

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
python3 -m unittest -q
# OK

./teamos doctor
# PASS (db: SKIP TEAMOS_DB_URL not set)
```

## 证据

- 日志/截图/报告路径：
  - `.team-os/logs/tasks/TEAMOS-0008/04_test.md`
