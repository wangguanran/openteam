# TEAMOS-0009 - 03 Work

- 标题：TEAMOS-CENTRAL-MODEL-ALLOWLIST
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 新增 Brain 模型 allowlist：`.team-os/policies/central_model_allowlist.yaml`
  - 新增离线资格检查 pipeline：`.team-os/scripts/pipelines/cluster_election.py qualify`
  - Control Plane 选主闸门：`cluster_manager.attempt_elect` 在 cluster enabled 时强制 allowlist 校验
  - Cluster status 输出 `llm_profile` 与 `leader_qualification`
  - CLI：`teamos cluster status` 增强输出；新增 `teamos cluster qualify`
  - 回归测试：`tests/test_central_model_allowlist.py`
- 关键命令（含输出摘要）：
  - `python3 -m unittest -q`：PASS
  - `./teamos --help`：cluster 子命令包含 qualify
- 决策与理由：
  - cluster enabled 模式下未设置 `TEAMOS_LLM_MODEL_ID` 或不在 allowlist 时禁止竞选 Brain（fail-safe）

## 变更文件清单

- `.team-os/policies/central_model_allowlist.yaml`
- `.team-os/scripts/pipelines/cluster_election.py`
- `.team-os/templates/runtime/orchestrator/app/cluster_manager.py`
- `.team-os/templates/runtime/orchestrator/app/main.py`
- `teamos`
- `tests/test_central_model_allowlist.py`
