# TASK-20260216-233035 - 04 Test

- 标题：TEAMOS-SCRIPT-PIPELINES
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 单元测试（offline）：`python3 -m unittest`
- 合规闸门：`teamos policy check`、repo purity
- Requirements 决定性校验：`requirements_raw_first verify`
- Prompt 编译幂等：重复执行 `prompt compile` 应 `changed=false`
- doctor：Control Plane openapi 覆盖检查通过

## 执行记录

```bash
# 命令 + 结果摘要（不要粘贴 secrets）
python3 -m unittest -q                           # OK
TEAMOS_SELF_IMPROVE_DISABLE=1 ./teamos policy check --quiet   # PASS
python3 .team-os/scripts/pipelines/repo_purity_check.py --json # ok=true
python3 .team-os/scripts/pipelines/requirements_raw_first.py verify --scope teamos # ok=true
TEAMOS_SELF_IMPROVE_DISABLE=1 ./teamos prompt compile --scope teamos # second run changed=false
TEAMOS_SELF_IMPROVE_DISABLE=1 ./teamos doctor    # PASS (api_coverage OK)
```

## 证据

- 生成物：
  - `docs/team_os/REPO_UNDERSTANDING.md`
  - `prompt-library/teamos/MASTER_PROMPT.md`
  - `prompt-library/teamos/prompt_manifest.json`
