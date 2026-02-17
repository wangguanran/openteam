# TEAMOS-0006 - 04 Test

- 标题：DETERMINISTIC-GOV-AUDIT-v2
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 决定性审计生成器可运行，并包含新增治理 controls
- 回归闸门：unittest + policy check + doctor

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
python3 -m unittest -q         # PASS
./teamos policy check          # PASS
./teamos doctor                # PASS
./teamos audit deterministic-gov  # PASS (writes docs/audits/...)
```

## 证据

- 日志/截图/报告路径：
  - `docs/audits/DETERMINISTIC_GOV_AUDIT_20260217T020711Z.md`
