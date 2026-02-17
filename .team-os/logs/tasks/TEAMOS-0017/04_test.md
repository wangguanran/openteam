# TEAMOS-0017 - 04 Test

- 标题：Hotfix: restore task creation pipeline
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- `task_create.py` 语法与最小行为验证
- 回归：repo policy_check / purity / unittest

## 执行记录

```bash
python3 -m py_compile .team-os/scripts/pipelines/task_create.py
# OK

./teamos task new --scope teamos --title "Hotfix: restore task creation pipeline (smoke)" --workstreams "platform" --dry-run
# OK (dry-run, no files written)

python3 -m unittest -q
# OK (33 tests)
```

## 证据

- 日志/截图/报告路径：
  - `.team-os/ledger/tasks/TEAMOS-0017.yaml`
  - `.team-os/logs/tasks/TEAMOS-0017/metrics.jsonl`
