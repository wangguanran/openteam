# 执行手册 (Team OS + Runtime)

本文面向“用 codex CLI 进行部署与长期运维”的单机环境，默认使用 Docker Compose。

## 1. 你需要准备什么

- 机器：推荐单机 Linux 或 macOS（本仓库在 macOS 上可用；生产建议 Linux）
- 软件：
  - `git`
  - `docker` + `docker compose`
  - `python3` + `pip3`（可选；用于本机直接运行 Control Plane/脚本/测试）
  - （可选）`gh`（自动开 issue/PR）
- 账号与权限：
  - Codex CLI OAuth 登录（默认 LLM 认证方式；执行 `codex login` / `codex login --device-auth`；不入库）
  - （可选）OpenAI API Key（仅在你显式允许 fallback 时使用；只能放本地 `.env` 或环境变量，不入库）
  - （可选）GitHub Token（仅用于 `gh`/API 操作，不入库）

## 2. 推荐执行环境

- 单机 + Docker Compose（未来可迁移到 K8s；后续会补充迁移 playbook）
- 运行时组件默认绑定本机端口，**对外网暴露属于高风险动作，必须审批**

## 3. 组件说明 (Runtime)

- Control Plane（Python + FastAPI + OpenAI Agents SDK）：控制平面，多角色协作与审计落盘；对外提供 HTTP API 供 CLI 查询/对话/注入需求
- OpenHands Agent Server：隔离执行平面
- Temporal：Durable workflow
- Postgres：Temporal DB + 运行时元数据预留（Control Plane 的 agent_registry/events 默认使用本地 SQLite：`.team-os/state/runtime.db`；后续可迁移到 Postgres）

## 4. 日常操作 (Team OS 仓库)

进入仓库并自检：

```bash
cd team-os
./scripts/teamos.sh doctor
```

创建新任务：

```bash
cd team-os
./scripts/teamos.sh new-task "一句话需求标题"
```

复盘与自我升级：

```bash
cd team-os
./scripts/teamos.sh retro <TASK_ID>
./scripts/teamos.sh self-improve
```

## 5. Runtime 部署与运维 (team-os-runtime)

Runtime 目录：`../team-os-runtime`

在新环境创建 runtime 目录（推荐，从 Team OS 模板生成；runtime 目录本身不作为 git repo）：

```bash
cd team-os
./scripts/teamos.sh runtime-init
# 可选：自动生成本地 secrets（不回显、不入库）
./scripts/teamos.sh runtime-secrets
cd ../team-os-runtime
make up
make ps
```

### 5.1 Control Plane（控制平面）与 teamos CLI

Control Plane HTTP API 默认只监听本机端口（建议 `127.0.0.1:8787`），用于：

- 查询运行态：focus/agents/tasks/runs/pending decisions
- 注入对话与新增需求（NEW_REQUIREMENT）
- 需求登记与冲突检测（冲突自动进入 NEED_PM_DECISION）

健康检查：

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/v1/status
```

初始化 CLI profile 并查看状态：

```bash
cd team-os
./teamos config init
./teamos status
```

交互式对话（支持 `/req` 注入需求）：

```bash
cd team-os
./teamos chat --project teamos
# 输入：/req 新增需求文本
# 输入：/quit 退出
```

启动（需要先创建 `.env`，不要入库）：

```bash
cd team-os-runtime
cp .env.example .env
# 编辑 .env，至少填写 POSTGRES_PASSWORD（或使用 `cd team-os && ./scripts/teamos.sh runtime-secrets` 自动生成）
make up
make ps
make logs
```

### 5.2 需求登记与冲突处理（Requirements）

Team OS 强制 **Repo vs Workspace** 边界：

- `team-os/` git 仓库：只允许 scope=`teamos` 的真相源（Team OS 自身）。
- 任何 scope=`project:<id>` 的 requirements/冲突报告/ledger/logs/prompts/plan/项目 repo workdir：必须在 **Workspace**（不在 `team-os/` 目录树内）。

默认 Workspace 路径：

- `~/.teamos/workspace`（可通过 `~/.teamos/config.toml` 或 CLI `--workspace-root` 覆盖）

project_id 约束（跨平台文件系统安全）：

- 必须为小写：`[a-z0-9][a-z0-9_-]{0,63}`
- 原因：macOS 默认文件系统大小写不敏感，`DEMO`/`demo` 会发生目录冲突

目录约定：

- Team OS 自身（scope=`teamos`）：`team-os/docs/teamos/requirements/**`
- 项目（scope=`project:<id>`）：`<WORKSPACE>/projects/<id>/state/requirements/**`
  - `requirements.yaml`（机读事实源）
  - `REQUIREMENTS.md`（人读汇总）
  - `conflicts/`（冲突报告）
  - `CHANGELOG.md`（变更日志）

首次使用请先初始化 Workspace：

```bash
cd team-os
./teamos config init
./teamos workspace init
./teamos workspace doctor
```

说明：

- `teamos workspace init` 幂等，可用于“修复”已存在项目目录的缺失结构（例如补齐 `repo/`、`state/prompts/MASTER_PROMPT.md`、`state/kb/`、`state/cluster/` 等）。

新增需求（两种方式等价）：

```bash
cd team-os
./teamos req add "需求文本" --project demo --workstream devops --priority P1
```

或：

```bash
cd team-os
./teamos chat --project demo
# 输入：/req 需求文本
```

查看与处理冲突：

```bash
cd team-os
./teamos req list --project demo --show-conflicts
./teamos req conflicts --project demo
```

当出现 `NEED_PM_DECISION`：

1. 打开冲突报告（路径会在 CLI 输出或 `/v1/status.pending_decisions` 中给出；文件位于 `<WORKSPACE>/projects/<id>/state/requirements/conflicts/*.md`）。
2. PM 拍板并落盘决策：更新该项目的 `requirements.yaml`（将被否决的需求标记为 `DEPRECATED`，并补齐 `supersedes/conflicts_with/decision_log_refs`）。

### 5.3 Profiles（多实例）与 Workstream（多平台协作域）

Profiles 用于在同一台机器上管理多个 Team OS 实例（例如不同环境/不同项目组）：

- CLI 配置文件：`~/.teamos/config.toml`
- 添加/切换：

```bash
cd team-os
./teamos config add-profile dev http://127.0.0.1:8787 --default-project-id demo
./teamos config use dev
./teamos config show
```

Workstream 是“平台/模块协作域”的一级概念，用于让 agents/tasks/requirements 可过滤、可并行协作：

- 登记表：`.team-os/state/workstreams.yaml`
- 约束：任务台账必须填写 `workstream_id`；需求必须包含 `workstreams`
- 常用过滤示例：

```bash
cd team-os
./teamos status --project demo --workstream ai
./teamos agents --project demo --workstream devops
./teamos tasks --project demo --workstream web
```

### 5.4 面板（GitHub Projects）与同步（Panel Sync）

GitHub Projects v2 是 Team OS 的 **主面板/视图层**，用于：

- Table: Backlog（计划与字段齐全）
- Board: Delivery（按状态列看推进）
- Roadmap: Timeline（里程碑/目标日期）

重要原则：

- Projects 只是视图层，系统真相源仍是：
  - scope=`teamos`（Team OS 自身）：`team-os/.team-os/ledger`、`team-os/docs/teamos/requirements/**`
  - scope=`project:<id>`（项目）：`<WORKSPACE>/projects/<id>/state/{ledger,logs,requirements,plan,prompts}`
  - Control Plane 的运行态状态库（SQLite）：`team-os/.team-os/state/runtime.db`（可迁移 Postgres）
- Projects 必须可随时从真相源 **全量重建/重同步**（不依赖 Projects 本身的编辑为事实来源）。

#### 5.4.1 创建/绑定一个 Project（每个 `project_id` 一个）

1) 在 GitHub 创建 Project v2（推荐 Org Project；Repo Project 也可）

- Org Project：适合多仓/多团队
- Repo Project：适合单仓范围

2) 在本仓库登记映射（真相源）

编辑：`.team-os/integrations/github_projects/mapping.yaml`，为你的 `project_id` 填入：

- `owner_type`（ORG/USER/REPO）
- `owner`（org/user login）
- `project_number`（Project v2 number）
- `project_url`（可选，但推荐填，便于 CLI `panel open`）

> 提示：`project_node_id` 可不填；Control Plane 会在真实同步时通过 GraphQL 自动解析。

#### 5.4.2 字段与视图（建议手工在 UI 创建视图；字段可由 full sync 确保）

字段（最小集合）由 sync 负责创建/复用（默认创建为 TeamOS 自定义字段）：

- `TeamOS Status`（单选：Todo/In Progress/In Review/Blocked/Done）
- `Workstreams`（文本，逗号分隔；用 contains 过滤）
- `Risk`（单选：LOW/MED/HIGH）
- `Need PM Decision`（单选：Yes/No）
- `Current Focus`（文本）
- `Active Agents`（数字）
- `Last Heartbeat`（文本，ISO-8601）
- `Start Date`/`Target Date`（日期，用于 Roadmap）
- `Task ID`（文本，稳定主键，用于重同步）
- `Links`（文本）

视图（至少 3 个，建议手工创建）：

1. Table: Backlog
2. Board: Delivery（按 `TeamOS Status` 分列）
3. Roadmap: Timeline（使用 `Start Date`/`Target Date`）

另外建议加一个过滤视图（可选）：

- `NEED_PM_DECISION`：过滤 `Need PM Decision = Yes`

#### 5.4.3 认证（OAuth 优先）

推荐 OAuth（通过 GitHub CLI 登录）：

```bash
gh auth status
export GITHUB_TOKEN="$(gh auth token -h github.com)"
```

然后把 `GITHUB_TOKEN` 写入本地 `team-os-runtime/.env`（不入库）或通过环境变量注入。

#### 5.4.4 同步（dry-run / full / incremental）

dry-run（不触网；只输出计划动作）：

```bash
cd team-os
./teamos panel sync --project demo --dry-run --full
```

真实同步（会写入 GitHub Projects；建议先 dry-run 再执行）：

> 安全闸门：Control Plane 默认禁用 GitHub 远程写入。执行真实同步前，需在 runtime 环境显式设置 `TEAMOS_PANEL_GH_WRITE_ENABLED=1`。

```bash
cd team-os
./teamos panel sync --project demo --full
./teamos panel sync --project demo
./teamos panel show --project demo
```

#### 5.4.5 n8n（可选：通知/自动化补充，不是主面板）

如果你需要把“需要 PM 决策/状态变化”等事件转发到 Slack/飞书/邮件，可选启用 n8n webhook（仅用于通知与自动化，不承担计划面板）。

1) 在 runtime `.env` 配置 webhook：

```bash
N8N_WEBHOOK_URL="https://<your-n8n>/webhook/<id>"
```

2) Control Plane 会 best-effort POST JSON 事件（失败不影响主流程）。当前已实现的事件：

- `need_pm_decision`：新增需求冲突进入 `NEED_PM_DECISION`
- `task_state_changed`：run 状态变化（PAUSE/RESUME/STOP）

预留（TODO）：

- `release_completed`
- `incident_opened`

安全要求见：`docs/SECURITY.md`（内网部署、最小权限、及时升级）。

#### 5.4.6 自动同步（可选：30~60s 刷新；会写入 Projects）

启用后台自动同步（30~60s 级别刷新；会写入 Projects）：

在 `team-os-runtime/.env` 中设置：

```bash
TEAMOS_PANEL_GH_WRITE_ENABLED=1
TEAMOS_PANEL_GH_AUTO_SYNC=1
TEAMOS_PANEL_GH_SYNC_INTERVAL_SEC=60
```

#### 5.4.7 排障

- `panel show` 显示未配置：检查 `.team-os/integrations/github_projects/mapping.yaml` 是否包含该 `project_id`
- `sync` 报 auth 错误：检查 `GITHUB_TOKEN` 是否存在（推荐 `gh auth token` 获取 OAuth token）
- GitHub rate limit：降低 `TEAMOS_PANEL_GH_SYNC_INTERVAL_SEC` 频率，或减少单次同步项数量（拆项目/分 workstream）

### 5.5 集群（多机协作，Brain/Assistant）

多机协作集群采用 GitHub-first 控制总线（Issue Lease + 节点 registry），并要求可接管/可恢复。

相关手册：

- 集群运行与接管：`docs/CLUSTER_RUNBOOK.md`
- 加新节点：`docs/NODE_BOOTSTRAP.md`
- 新仓库 bootstrap / 非空仓库 upgrade 闸门：`docs/REPO_BOOTSTRAP_AND_UPGRADE.md`

安全闸门：

- 选主/节点心跳/任务 lease 等属于 GitHub 远程写操作，默认必须通过 env gate 显式启用（详见 `.team-os/cluster/config.yaml`）。
- 远程机器安装依赖/写 systemd/启动服务属于高风险动作，必须审批后执行。

停止：

```bash
cd team-os-runtime
make down
```

升级（需要审批后执行，避免 `latest` 漂移；建议先阅读变更日志/风险）：

```bash
cd team-os-runtime
docker compose pull
docker compose up -d
docker compose ps
```

备份（最小可用：备份 Postgres；注意不要把备份文件入库）：

```bash
cd team-os-runtime
mkdir -p backups
# 备份 temporal DB（也可改成 pg_dumpall）
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d temporal > backups/temporal_$(date +%Y%m%d_%H%M%S).sql
```

恢复（示例）：

```bash
cd team-os-runtime
cat backups/temporal_<timestamp>.sql | docker compose exec -T postgres psql -U "$POSTGRES_USER" -d temporal
```

> 说明：Temporal 会使用多个 DB（含 visibility）。如需完整恢复，请按实际 DB 列表分别 dump/restore，并在任务日志中记录操作与结果。

健康检查（示例）：

```bash
curl -fsS http://127.0.0.1:8787/healthz
```

OpenHands 健康检查（示例）：

```bash
curl -fsS http://127.0.0.1:18000/alive
```

Temporal UI（示例）：

```bash
open http://127.0.0.1:18081
```

## 6. 部署验证 (需要落盘证据)

将实际执行的命令与验证结果摘要写入本节（不要落盘 secrets）。

```bash
cd team-os-runtime
make pull
make up
make ps
```

已验证（2026-02-15，本机 localhost 绑定）：

- `postgres/temporal/temporal-ui/openhands-agent-server/control-plane` 均为 `running/healthy`
- Control Plane：`curl -fsS http://127.0.0.1:8787/healthz` 返回 `{"status":"ok", ...}`
- OpenHands Agent Server：`curl -fsS http://127.0.0.1:18000/alive` 返回 `{"status":"ok"}`
- Temporal UI：`http://127.0.0.1:18081`

新增（Control Plane + CLI）验收点（完成后在此落证据）：

- Control Plane：`curl -fsS http://127.0.0.1:8787/v1/status`
- CLI：`cd team-os && ./teamos status`

## 7. 新任务怎么开始 (Genesis)

标准顺序（最小闭环）：

1. `new-task`：生成台账与 `00~02` 日志骨架
2. Intake：澄清范围、风险、闸门、依赖
3. 如需外部最新信息：执行 Skill Boot（检索 -> 来源摘要 -> Skill Card -> 记忆索引）
4. 进入 Delivery：实现/测试/审查/发布/观测
5. Retro：输出改进点并落入 Self-Improve 工作流

## 8. 角色如何扩展

1. 复制模板：`.team-os/templates/role.md` -> `.team-os/roles/<Role>.md`
2. 补齐职责/输入输出/权限/产物/DoR/DoD/Skill Boot 要求/记忆规则
3. 执行一次 Skill Boot（可以先写 TODO 占位，但必须落盘）

## 9. 工作流如何扩展

1. 复制模板：`.team-os/templates/workflow.yaml` -> `.team-os/workflows/<Workflow>.yaml`
2. 明确状态机、步骤、角色映射、产物、闸门、退出条件
3. 在相关任务中试运行并在 Retro 中修订

## 10. 自我升级怎么做

1. 在 `07_retro.md` 写清“Team OS 的缺陷/改进点”
2. 运行 `./scripts/teamos.sh self-improve` 生成自我升级条目
3. 若 `gh` 可用且已登录：优先创建 issue/PR；否则生成 pending 草稿到：
   - `.team-os/ledger/team_os_issues_pending/`

## 11. 安全闸门

详见 `docs/SECURITY.md`。重点：

- 生产发布/打开公网端口/数据删除与覆盖/密钥处理：必须审批
- 禁止 secrets 入库：只允许 `.env.example` 入库
- 外部文档不可信：只抽取事实并落盘来源摘要

## 12. GitHub (gh) 使用 (可选)

查看登录状态：

```bash
gh auth status
```

在 Team OS 仓库创建 issue（如已配置 remote）：

```bash
cd team-os
./scripts/teamos.sh self-improve
```

## 13. 常见故障排查

- Docker/Compose 不可用：确认 Docker Desktop 运行；执行 `docker info`
- 端口冲突：用 `lsof -iTCP -sTCP:LISTEN -n -P | rg <port>`
- Temporal 不可用：查看 `docker compose logs temporal` 与 `docker compose logs temporal-ui`
- OpenHands 不可用：查看 `docker compose logs openhands-agent-server`，确认是否需要 docker socket（风险见 `docs/SECURITY.md`）
- Control Plane 健康检查失败：查看 `docker compose logs control-plane`

## 14. TODO

- OpenTelemetry 接入（trace/metrics/log correlation）
- K8s 部署与最小权限方案
- Postgres 索引与检索加速（任务台账/知识库索引）
