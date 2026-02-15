# TEAMOS.md (通用 AI 开发团队操作系统规范)

> 目标：在单机上长期运行一个可审计、可扩展、可自我升级的“通用 AI 开发团队操作系统 (Team OS)”，并通过 `team-os-runtime` 提供 24/7 运行时。

## 1. 组件与边界

### 1.1 控制平面 (Control Plane)

- **Orchestrator**：基于 OpenAI Agents SDK（Python）驱动多角色协作与审计。
- Orchestrator 只负责：读取角色/工作流定义、生成/更新台账与日志、调用执行平面、记录证据。
- **Control Plane HTTP API**：对外提供可审计的查询与注入接口（focus/agents/tasks/requirements/chat），供 `teamos` CLI 使用。
- OAuth 默认：LLM 相关能力优先复用 Codex CLI 的 ChatGPT OAuth（见 `docs/AUTH.md`）。

### 1.2 执行平面 (Execution Plane)

- **OpenHands Agent Server**：隔离执行构建/测试/脚本，降低对宿主机的破坏风险。

### 1.3 长流程与持久化

- **Temporal**：Durable workflow、失败重试、任务状态机持久化（默认启用）。
- **Postgres**：Temporal DB + 运行时元数据与索引预留；MVP 阶段 Control Plane 的 agent_registry/events 默认落盘到 SQLite（`.team-os/state/runtime.db`），后续可迁移到 Postgres；知识库与记忆的“最终事实来源”仍以 git 文件为准。

### 1.4 观测 (MVP)

- 最小可用：落盘日志 + healthcheck。
- 预留：OpenTelemetry（见 `docs/EXECUTION_RUNBOOK.md` 的 TODO）。

## 2. Team OS 目录模型

Team OS 以文件系统为“真相源”，结构见 `AGENTS.md` 与仓库内 `.team-os/`。

关键资产：

- 角色定义：`.team-os/roles/*.md`
- 工作流定义：`.team-os/workflows/*.yaml`
- 知识库：`.team-os/kb/**`
  - `sources/`：来源摘要（可追溯）
  - `roles/`：按角色沉淀的 Skill Cards
  - `platforms/`：按平台/子系统沉淀的 Skill Cards
- 角色长期记忆：`.team-os/memory/roles/<Role>/index.md`
- 台账：`.team-os/ledger/**`
- 任务日志：`.team-os/logs/tasks/<TASK_ID>/**`
- 运行态状态（实例/Focus/项目/Workstream）：`.team-os/state/**`
- 需求主文档（按 project_id）：`docs/requirements/<project_id>/**`
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
4. `work` -> `03_work.md`
5. `test` -> `04_test.md`
6. `release` -> `05_release.md`
7. `observe` -> `06_observe.md`
8. `retro` -> `07_retro.md`
9. `closed`

台账文件 `.team-os/ledger/tasks/<TASK_ID>.yaml` 必须反映当前状态。

## 5. Skill Boot (检索 -> 沉淀) 标准

触发条件：

- 新平台/新子系统/新风险域
- 依赖“最新事实”的信息（镜像名、端口、参数、规范、政策）
- 任务中出现重复踩坑

必产物（见 `AGENTS.md`）：

1. 来源摘要：`.team-os/kb/sources/`
2. Skill Card：`.team-os/kb/roles/` 或 `.team-os/kb/platforms/`
3. 角色记忆索引：`.team-os/memory/roles/<Role>/index.md`

文件命名建议：

- `<YYYYMMDD>_<slug>.md`（slug 只用 `a-z0-9-_`）

## 6. 扩展机制

### 6.1 扩展角色

新增角色文件到 `.team-os/roles/`（基于模板 `templates/role.md`），并执行一次 Skill Boot（即便先写占位卡片与 TODO）。

### 6.2 扩展工作流

新增工作流 YAML 到 `.team-os/workflows/`（基于模板 `templates/workflow.yaml`），要求包含：

- 状态机
- 步骤（step）
- 角色映射
- 产物清单
- 闸门与退出条件

## 7. 命令入口

统一入口：`./scripts/teamos.sh`

- `doctor`：环境自检
- `new-task "<title>"`：生成任务台账与日志骨架
- `skill-boot "<role>" "<topic_or_platform>"`：生成 Skill Boot 落盘骨架
- `retro "<task_id>"`：生成/打开复盘日志
- `self-improve`：生成自我升级条目并尝试创建 issue（可选）

运行态 CLI：`./teamos`

- `./teamos config init|add-profile|use|show`
- `./teamos status|focus|agents|tasks|chat|req ...|doctor`
