# 执行手册 (Team OS + Runtime)

本文面向“用 codex CLI 进行部署与长期运维”的单机环境，默认使用 Docker Compose。

## 1. 你需要准备什么

- 机器：推荐单机 Linux 或 macOS（本仓库在 macOS 上可用；生产建议 Linux）
- 软件：
  - `git`
  - `docker` + `docker compose`
  - `python3` + `pip3`（Runtime orchestrator 需要）
  - （可选）`gh`（自动开 issue/PR）
- 账号与权限：
  - OpenAI API Key（放到本地 `.env` 或环境变量，不入库）
  - （可选）GitHub Token（仅用于 `gh`/API 操作，不入库）

## 2. 推荐执行环境

- 单机 + Docker Compose（未来可迁移到 K8s；后续会补充迁移 playbook）
- 运行时组件默认绑定本机端口，**对外网暴露属于高风险动作，必须审批**

## 3. 组件说明 (Runtime)

- Orchestrator（Python + OpenAI Agents SDK）：控制平面，多角色协作与审计落盘
- OpenHands Agent Server：隔离执行平面
- Temporal：Durable workflow
- Postgres：运行时元数据与索引（知识库与记忆仍以 git 文件为准）

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

启动（需要先创建 `.env`，不要入库）：

```bash
cd team-os-runtime
cp .env.example .env
make up
make ps
make logs
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
curl -fsS http://127.0.0.1:18080/healthz
```

## 6. 部署验证 (需要落盘证据)

在获得审批后执行并将摘要写入本节：

```bash
cd team-os-runtime
docker compose pull
docker compose up -d
docker compose ps
```

预期：

- `postgres/temporal/openhands/orchestrator` 均为 `running`
- Orchestrator `GET /healthz` 返回 `ok`

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
- Orchestrator 健康检查失败：查看 `docker compose logs orchestrator`

## 14. TODO

- OpenTelemetry 接入（trace/metrics/log correlation）
- K8s 部署与最小权限方案
- Postgres 索引与检索加速（任务台账/知识库索引）
