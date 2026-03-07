# team-os-runtime

单机运行时环境（Docker Compose），用于 24/7 跑：

- Control Plane（Python + FastAPI + CrewAI orchestrator）
- OpenHands Agent Server（隔离执行平面）
- Temporal + UI（兼容组件，可选）
- Postgres（Temporal DB + 运行时元数据预留）

## 安全提示 (先读)

- 本目录仅提交 `.env.example`，真实 `.env` 不得入库。
- `openhands-agent-server` 默认挂载 Docker socket（高风险能力）。不要暴露到公网；任何公网暴露属于审批项。
- Control Plane 默认通过挂载 `${HOME}/.codex` 复用 Codex OAuth 登录态（本机文件，不入库）。如未登录：先执行 `codex login` 或 `codex login --device-auth`。

## Repo vs Workspace（硬隔离）

Team OS 有两个层级的“真相源”：

- **Team OS 自身**（scope=`teamos`）：业务真相源可落盘在 `team-os/` git 仓库（例如 `docs/teamos/requirements`）。
- **运行态文件**（runtime state/db/ledger/logs/cluster）：统一落盘在 `team-os-runtime/`（不再写入 `team-os/.team-os`）。
- **任何项目**（scope=`project:<id>`）：必须落盘在 **Workspace**（不在 `team-os/` 目录树内）。

默认 Workspace 路径：

- `~/.teamos/workspace`

Runtime 会挂载宿主机 Workspace 到容器内：

- host: `${HOME}/.teamos/workspace`
- container: `/teamos-workspace`
- env: `TEAMOS_WORKSPACE_ROOT=/teamos-workspace`

在启动 runtime 前，建议先初始化 Workspace：

```bash
cd ../team-os
./teamos workspace init
./teamos workspace doctor
```

## 启动/停止

```bash
cd team-os-runtime
cp .env.example .env
# 编辑 .env，至少填写 POSTGRES_PASSWORD，按需填写 OPENAI_API_KEY
make up
make ps
```

停止：

```bash
cd team-os-runtime
make down
```

## 日志与健康检查

```bash
cd team-os-runtime
make logs
```

Control Plane health：

```bash
curl -fsS http://127.0.0.1:${CONTROL_PLANE_PORT:-8787}/healthz
curl -fsS http://127.0.0.1:${CONTROL_PLANE_PORT:-8787}/v1/status
```

Temporal UI：

```bash
open http://127.0.0.1:${TEMPORAL_UI_PORT:-18081}
```

OpenHands Agent Server health（若暴露到宿主）：

```bash
curl -fsS http://127.0.0.1:${OPENHANDS_AGENT_SERVER_PORT:-18000}/alive
```

## Makefile

- `make up` / `make down`
- `make pull`
- `make ps`
- `make logs`
- `make doctor`

## teamos CLI（在 team-os 仓库）

```bash
cd ../team-os
./teamos config init
./teamos status
./teamos chat --project teamos
./teamos panel show --project demo
./teamos panel sync --project demo --dry-run --full
```

## GitHub Projects 面板（可选）

1) 配置映射文件（真相源仍在本仓库；Projects 是视图层）：

- `integrations/github_projects/mapping.yaml`

2) 配置 GitHub Token（推荐 OAuth）：

```bash
# 推荐：从 gh 取 OAuth token（不要写入 git）
export GITHUB_TOKEN="$(gh auth token -h github.com)"
```

3) 启用后台同步（会对 GitHub Projects 写入 item/字段，属于“视图层变更”）：

在 `team-os-runtime/.env` 中设置：

```bash
TEAMOS_PANEL_GH_WRITE_ENABLED=1
TEAMOS_PANEL_GH_AUTO_SYNC=1
```

## Hub APIs

Control Plane now exposes hub APIs for presentation/orchestration layers:

- `GET /v1/hub/status`
- `GET /v1/hub/migrations`
- `GET /v1/hub/locks`
- `GET /v1/hub/approvals`
- `GET /v1/runs`
- `POST /v1/runs/start`
- `POST /v1/self_upgrade/run` (`/v1/self_improve/run` is kept as a compatibility alias)
- `GET /v1/self_upgrade/proposals`
- `POST /v1/self_upgrade/proposals/decide`

Default behavior:

- `control-plane` startup triggers one CrewAI self-upgrade run for the current `team-os` repo.
- `control-plane` also keeps a continuous self-upgrade loop running for the current repo.
- The self-upgrade run can also target another local repo via `repo_path`.
- Bug findings are materialized immediately.
- Feature findings become proposals, wait for user confirmation, and respect a 1 hour cooldown before materialization.
- Process optimizations collect telemetry for 24 hours before materialization.
- Findings are recorded into Team OS task ledgers and, when GitHub auth is available, mirrored to GitHub issues plus the configured GitHub Project panel.
