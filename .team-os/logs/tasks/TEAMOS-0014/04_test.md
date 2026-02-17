# TEAMOS-0014 - 04 Test

- 标题：TEAMOS-RAW-FEASIBILITY-V3
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- Raw-First v3：raw capture + feasibility 报告 + raw_assessments 旁路索引
- gating：NEEDS_INFO/NOT_FEASIBLE 不进入 Expanded 可执行条目，仅创建 NEED_PM_DECISION
- system/self-improve：不写 raw_inputs.jsonl

## 执行记录

```bash
python3 -m unittest -q
# OK (28 tests)
```

## 证据

- 日志/截图/报告路径：
  - evals: `evals/test_requirements_raw_first.py`
