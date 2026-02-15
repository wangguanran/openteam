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
./teamos chat --project DEMO
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

每个项目（`project_id`）的需求主文档位于：

- `docs/requirements/<project_id>/requirements.yaml`（机读事实源）
- `docs/requirements/<project_id>/REQUIREMENTS.md`（人读汇总）
- `docs/requirements/<project_id>/conflicts/`（冲突报告）
- `docs/requirements/<project_id>/CHANGELOG.md`（变更日志）

新增需求（两种方式等价）：

```bash
cd team-os
./teamos req add "需求文本" --project DEMO --workstream ai --priority P1
```

或：

```bash
cd team-os
./teamos chat --project DEMO
# 输入：/req 需求文本
```

查看与处理冲突：

```bash
cd team-os
./teamos req list --project DEMO --show-conflicts
./teamos req conflicts --project DEMO
```

当出现 `NEED_PM_DECISION`：

1. 打开冲突报告（`docs/requirements/<project_id>/conflicts/*.md`），按 Option A/B/C 做决策。
2. 当前 MVP 默认由 PM 人工落盘决策结果：更新 `requirements.yaml`（例如将被否决的需求标记为 `DEPRECATED`，并补齐 `supersedes/conflicts_with/decision_log_refs`），然后提交变更。

### 5.3 Profiles（多实例）与 Workstream（多平台协作域）

Profiles 用于在同一台机器上管理多个 Team OS 实例（例如不同环境/不同项目组）：

- CLI 配置文件：`~/.teamos/config.toml`
- 添加/切换：

```bash
cd team-os
./teamos config add-profile dev http://127.0.0.1:8787 --default-project-id DEMO
./teamos config use dev
./teamos config show
```

Workstream 是“平台/模块协作域”的一级概念，用于让 agents/tasks/requirements 可过滤、可并行协作：

- 登记表：`.team-os/state/workstreams.yaml`
- 约束：任务台账必须填写 `workstream_id`；需求必须包含 `workstreams`
- 常用过滤示例：

```bash
cd team-os
./teamos status --project DEMO --workstream ai
./teamos agents --project DEMO --workstream devops
./teamos tasks --project DEMO --workstream web
```

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
