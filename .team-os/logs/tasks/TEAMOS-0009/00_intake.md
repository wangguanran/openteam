# TEAMOS-0009 - 00 Intake

- 标题：TEAMOS-CENTRAL-MODEL-ALLOWLIST
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 增加“中央大脑模型资格”机制：维护 allowlist，并在集群选主前强制校验，不满足则禁止竞选 Brain。

## 目标/非目标

- 目标：
- 新增真相源策略：`.team-os/policies/central_model_allowlist.yaml`
- 新增确定性 pipeline：`.team-os/scripts/pipelines/cluster_election.py qualify`（基于 allowlist 的资格判断）
- Control Plane 集群选主闸门：`cluster_manager.attempt_elect` 在 cluster enabled 时必须先校验 allowlist
- Cluster status 输出包含 `llm_profile` + `leader_qualification`
- CLI 提供 `teamos cluster qualify`（离线资格检查）与 `teamos cluster status` 打印资格信息
- 非目标：
- 本任务不实现 DB-first leader lease / 心跳 / 接管（另起任务）。
- 本任务不实现节点注册 llm_profile 持久化（仅状态输出 + 选主闸门）。

## 约束与闸门

- 风险等级：R2（集群/治理关键路径变更：选主资格闸门）
- 需要审批的动作（如有）：无（不执行高风险动作；仅代码变更）

## 澄清问题 (必须回答)

- Q: 未设置 `TEAMOS_LLM_MODEL_ID` 时如何处理？A: cluster enabled 模式下禁止竞选 Brain（fail-safe）；cluster disabled 模式仅状态显示未满足资格。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/policies/central_model_allowlist.yaml`
- `.team-os/scripts/pipelines/cluster_election.py`
- `.team-os/templates/runtime/orchestrator/app/cluster_manager.py`
- `.team-os/templates/runtime/orchestrator/app/main.py`
- `teamos`（CLI：`cluster status` 输出资格；新增 `cluster qualify`）
- `tests/test_central_model_allowlist.py`
