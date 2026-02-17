# TEAMOS-0015 - 04 Test

- 标题：TEAMOS-SELF-IMPROVE-SEPARATION
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- system update channel：不写 raw_inputs / raw_assessments / feasibility 报告
- Self-Improve runner：系统 source 标记为 `SYSTEM_SELF_IMPROVE`

## 执行记录

```bash
python3 -m unittest -q
# OK (29 tests)
```

## 证据

- 日志/截图/报告路径：
  - evals: `evals/test_system_requirements_update.py`
