# TEAMOS-0005 - 04 Test

- 标题：TEAMOS-PROJECT-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- CLI 命令存在且可用：
  - `teamos project config init/show/set/validate`
  - `teamos project agents inject`
  - `teamos prompt build/diff`
- 决定性 pipelines 回归：
  - project config schema 校验
  - AGENTS.md 注入幂等/替换/保留原内容
- 全量回归：
  - `python3 -m unittest -q`
  - `./teamos policy check`
  - `./teamos doctor`

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
python3 -m unittest -q              # PASS
./teamos policy check               # PASS
./teamos doctor                     # PASS
./teamos project --help             # PASS (config/agents)
./teamos prompt --help              # PASS (compile/build/diff)
```

## 证据

- 日志/截图/报告路径：
  - `tests/test_project_config.py`
  - `tests/test_project_agents_inject.py`
