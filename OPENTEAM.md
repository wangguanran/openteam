# OPENTEAM.md (通用 AI 开发团队操作系统规范)

> 目标：在单机上长期运行一个可审计、可扩展、可自我升级的“通用 AI 开发团队操作系统 (OpenTeam)”，并通过 OpenTeam runtime（默认配置目录 `~/.openteam/runtime-config/default`）提供 24/7 运行时。

## 1. 组件与边界

### 1.1 控制平面 (Control Plane)

- **Orchestrator**：统一使用 CrewAI 作为流程编排引擎（Flow），并通过确定性 pipelines 执行落盘写入。
- Orchestrator 只负责：读取角色/Flow 定义、生成/更新台账与日志、调用执行平面、记录证据。
- **Control Plane HTTP API**：对外提供可审计的查询与注入接口（focus/agents/tasks/requirements/chat），供 `openteam` CLI 使用。
- OAuth 默认：LLM 相关能力优先复用 Codex CLI 的 ChatGPT OAuth（见 `docs/product/AUTH.md`）。

### 1.2 执行平面 (Execution Plane)

- **OpenHands Agent Server**：隔离执行构建/测试/脚本，降低对宿主机的破坏风险。

### 1.3 长流程与持久化

- **CrewAI Flow**：统一流程语义（Genesis/Delivery/Incident/Self-Improve 等），入口 `POST /v1/runs/start`。
- **Deterministic Pipelines**：所有会改变真相源的动作必须由 `scripts/pipelines/*.py` 执行。
- **Postgres/Hub**：集中式运行态、审批与锁等数据。
- **兼容运行组件（可选）**：Runtime 模板可保留 OpenHands 组件用于兼容场景，但不作为流程编排真相源。

### 1.4 观测 (MVP)

- 最小可用：落盘日志 + healthcheck。
- 预留：OpenTelemetry（见 `docs/runbooks/EXECUTION_RUNBOOK.md` 的 TODO）。

### 1.5 面板层 (Panel Layer: GitHub Projects v2)

- GitHub Projects v2 是 OpenTeam 的**主面板/视图层**（Table/Board/Roadmap），用于实时查看：
  - 当前 focus
  - 活跃 agents 与心跳
  - tasks 状态（RUNNING/BLOCKED/WAITING_PM 等）
  - NEED_PM_DECISION（冲突/决策点）
  - milestones（项目：`<WORKSPACE>/projects/<id>/state/plan/plan.yaml`；OpenTeam 自身：`docs/plans/openteam/plan.yaml`）
- **真相源分层（Repo vs Workspace）**：
  - scope=`openteam`：真相源允许落盘在 `openteam/` git 仓库内（用于 OpenTeam 自我升级与 Roadmap）
  - scope=`project:<id>`：真相源必须落盘在 **Workspace**（不在 `openteam/` 目录树内）
    - requirements：`<WORKSPACE>/projects/<id>/state/requirements/**`
    - ledger/logs：`<WORKSPACE>/projects/<id>/state/ledger/**`、`<WORKSPACE>/projects/<id>/state/logs/**`
    - prompts/plan/kb：`<WORKSPACE>/projects/<id>/state/prompts|plan|kb/**`
    - repo workdir：`<WORKSPACE>/projects/<id>/repo/**`
  - Control Plane 运行态状态与缓存：Docker named volumes（容器内 `/openteam-runtime/*`）
- Panel 必须可随时从真相源 **全量重建/重同步**：
  - 通过 Control Plane 的 `POST /v1/panel/github/sync`（`mode=full`）实现
  - 字段/状态/workstream 映射以 `integrations/github_projects/mapping.yaml` 为准

### 1.6 集群（Multi-node Cluster，进行中）

- 多机协作采用 GitHub-first 控制总线（Issue Lease + Node Registry + Task Lease）
- 目标：Brain/Assistant 可选主接管、异构能力路由、断点续跑恢复
- 相关手册：
  - `docs/runbooks/CLUSTER_RUNBOOK.md`
  - `docs/runbooks/NODE_BOOTSTRAP.md`
  - `docs/runbooks/REPO_BOOTSTRAP_AND_UPGRADE.md`

## 2. OpenTeam 目录模型

OpenTeam 以文件系统为“真相源”，结构见 `AGENTS.md` 与仓库内 `.openteam/`。

关键资产（以 **OpenTeam 自身仓库** 为准；项目 scope 的真相源在 Workspace 内）：

- 角色定义：`specs/roles/*.md`
- Crew Flow 定义：`specs/workflows/*.yaml`
- Prompt 规范与编译产物：`specs/prompts/openteam/**`
- 策略与 schema：`specs/policies/**`、`specs/schemas/**`
- 知识库：`.openteam/kb/**`
  - `sources/`：来源摘要（可追溯）
  - `roles/`：按角色沉淀的 Skill Cards
  - `platforms/`：按平台/子系统沉淀的 Skill Cards
- 角色长期记忆：`.openteam/memory/roles/<Role>/index.md`
- 台账（scope=openteam）：`.openteam/ledger/**`
- 任务日志（scope=openteam）：`.openteam/logs/tasks/<TASK_ID>/**`
- 项目台账/日志（scope=project:<id>）：`<WORKSPACE>/projects/<id>/state/ledger/**`、`<WORKSPACE>/projects/<id>/state/logs/**`
- 运行态状态（实例/Focus/项目/Workstream）：`.openteam/state/**`
- OpenTeam 自身需求主文档（scope=openteam）：`docs/product/openteam/requirements/**`
- 项目需求主文档（scope=project:<id>）：`<WORKSPACE>/projects/<id>/state/requirements/**`
  - `requirements.yaml`（机读事实源）
  - `REQUIREMENTS.md`（人读）
  - `conflicts/`（冲突报告）
  - `CHANGELOG.md`（变更日志）

## 3. 风险分级与闸门

风险等级建议：

- **R0**：纯文档/模板变更，无执行、无外部影响
- **R1**：本地开发/测试、可回滚、低风险
- **R2**：涉及 Docker/网络端口/依赖更新/自动化脚本执行
- **R3**：生产发布、数据迁移、密钥轮换、不可逆变更

R2/R3 及任何 Hard Rules 中列出的动作必须先获得批准（在任务日志中记录“批准内容 + 时间 + 批准人”）。

## 4. 任务状态机 (Canonical)

任务默认状态机与日志文件对应：

1. `intake` -> `00_intake.md`
2. `plan` -> `01_plan.md`
3. `todo` -> `02_todo.md`
4. `doing` -> `03_work.md`（别名：`running`/`work`/`in_progress`）
5. `test` -> `04_test.md`
6. `release` -> `05_release.md`
7. `observe` -> `06_observe.md`
8. `retro` -> `07_retro.md`
9. `closed`

台账文件 `.openteam/ledger/tasks/<TASK_ID>.yaml` 必须反映当前状态。

## 5. Skill Boot (检索 -> 沉淀) 标准

触发条件：

- 新平台/新子系统/新风险域
- 依赖“最新事实”的信息（镜像名、端口、参数、规范、政策）
- 任务中出现重复踩坑

必产物（见 `AGENTS.md`）：

1. 来源摘要：`.openteam/kb/sources/`
2. Skill Card：`.openteam/kb/roles/` 或 `.openteam/kb/platforms/`
3. 角色记忆索引：`.openteam/memory/roles/<Role>/index.md`

文件命名建议：

- `<YYYYMMDD>_<slug>.md`（slug 只用 `a-z0-9-_`）

## 6. 扩展机制

### 6.1 扩展角色

新增角色文件到 `specs/roles/`（基于模板 `templates/content/role.md`），并执行一次 Skill Boot（即便先写占位卡片与 TODO）。

### 6.2 扩展 Crew Flow

新增 Crew Flow YAML 到 `specs/workflows/`（基于模板 `templates/content/workflow.yaml`），要求包含：

- 状态机
- 步骤（step）
- 角色映射
- 产物清单
- 闸门与退出条件

## 7. 命令入口

统一入口：`./scripts/openteam.sh`

- `doctor`：环境自检
- `new-task "<title>"`：生成任务台账与日志骨架
- `skill-boot "<role>" "<topic_or_platform>"`：生成 Skill Boot 落盘骨架
- `retro "<task_id>"`：生成/打开复盘日志
- `self-improve`：生成自我升级条目并尝试创建 issue（可选）

运行态 CLI：`./openteam`

- `./openteam config init|add-profile|use|show`
- `./openteam status|focus|agents|tasks|chat|req ...|doctor`
