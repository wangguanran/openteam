# TEAMOS-0009 - 04 Test

- 标题：TEAMOS-CENTRAL-MODEL-ALLOWLIST
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 单元测试（stdlib unittest）：
  - allowlist 读取
  - 资格判断（缺 model_id / allow model）
- CLI 自检：
  - `teamos cluster status` 输出资格字段（需要 Control Plane）
  - `teamos cluster qualify` 离线资格检查（依赖 env `TEAMOS_LLM_MODEL_ID`）

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
python3 -m unittest -q
# OK

./teamos --help | head
# usage includes: cluster qualify
```

## 证据

- 日志/截图/报告路径：
  - `.team-os/logs/tasks/TEAMOS-0009/04_test.md`
