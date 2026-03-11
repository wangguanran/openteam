# TeamOS Runtime Config

单机运行时环境（Docker Compose），用于 24/7 跑：

- Control Plane（Python + FastAPI + CrewAI orchestrator）
- OpenHands Agent Server（隔离执行平面）
- Postgres（运行时元数据）

## 安全提示 (先读)

- 本目录仅提交 `.env.example`，真实 `.env` 不得入库。
- `openhands-agent-server` 默认挂载 Docker socket（高风险能力）。不要暴露到公网；任何公网暴露属于审批项。
- Control Plane 默认通过挂载 `${HOME}/.codex` 复用 Codex OAuth 登录态（本机文件，不入库）。如未登录：先执行 `codex login` 或 `codex login --device-auth`。

## Repo vs Workspace（硬隔离）

Team OS 有两个层级的“真相源”：

- **Team OS 自身**（scope=`teamos`）：业务真相源可落盘在 `team-os/` git 仓库（例如 `docs/product/teamos/requirements`）。
- **运行态文件**（runtime state/db/ledger/logs/cluster）：统一保存在 Docker named volumes（不再写入 `team-os/.team-os`，也不要求单独的 `team-os-runtime/` 目录）。
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
cd ~/.teamos/runtime-config/default
cp .env.example .env
# 外部数据库：填写 TEAMOS_DB_URL
# 本地数据库：保持 TEAMOS_DB_URL 为空；按需覆盖 POSTGRES_PASSWORD
# 按需填写 OPENAI_API_KEY
make up
make ps
```

如需把 CrewAI 的思考强度拉满，设置：

```bash
export TEAMOS_CREWAI_REASONING_EFFORT=xhigh
```

当前 runtime 默认使用 `openai-codex/gpt-5.4`，并将 `TEAMOS_CREWAI_REASONING_EFFORT` 设为 `xhigh`。

如需让本地 runtime 自动跟随 GHCR 上的新 `control-plane` 镜像，设置：

```bash
TEAMOS_CONTROL_PLANE_AUTO_UPDATE=1
TEAMOS_CONTROL_PLANE_AUTO_UPDATE_INTERVAL_SEC=300
TEAMOS_CONTROL_PLANE_AUTO_UPDATE_ONLY_IF_IDLE=0
```

启用后：

- `make up` 会自动拉起本地镜像 watcher
- watcher 会周期性执行 `docker compose pull control-plane`
- 检测到本地镜像更新后，会自动重建 `control-plane`
- 如需只在空闲时更新，可设置 `TEAMOS_CONTROL_PLANE_AUTO_UPDATE_ONLY_IF_IDLE=1`
- 日志落在 `auto_update/watcher.log`

手动查看/控制：

```bash
make auto-update-status
make auto-update-check
make auto-update-stop
make auto-update-start
```

停止：

```bash
cd ~/.teamos/runtime-config/default
make down
```

说明：

- `make up`：只使用 GitHub CI 发布的 `TEAMOS_CONTROL_PLANE_IMAGE` 镜像启动；本地不允许构建 control-plane 镜像
- `docker-compose.yml` 默认总会启动本地 `postgres` 容器
- 当 `TEAMOS_DB_URL` 为空时，control-plane 连接本地 `postgres`
- 当 `TEAMOS_DB_URL` 非空时，control-plane 改为直连外部数据库；本地 `postgres` 仍会随 compose 启动
- runtime state/hub/cache/tmp/worktrees 默认持久化在 Docker named volumes
- 宿主机默认只保留 runtime 配置目录：`~/.teamos/runtime-config/default`

## 镜像化启动（推荐给新机器）

如果只想直接拉镜像启动，不需要本地构建：

```bash
cd ../team-os
./scripts/runtime_up_image.sh
# 或者指定外部数据库：
./scripts/runtime_up_image.sh --db-url 'postgresql://user:password@host:5432/team_os'
```

默认会：

- 初始化 `~/.teamos/runtime-config/default`
- 生成/更新 `.env`
- 拉取 `ghcr.io/wangguanran/teamos-control-plane:main`
- 启动统一的 `docker-compose.yml`
- 若 `.env` 中启用了 `TEAMOS_CONTROL_PLANE_AUTO_UPDATE=1`，同步拉起本地镜像 watcher

数据库模式：

- 传入 `--db-url`：写入 `TEAMOS_DB_URL`，control-plane 直连外部数据库
- 不传 `--db-url`：保持 `TEAMOS_DB_URL` 为空，control-plane 使用本地 `postgres`

构建期和运行期网络是分离的：

- 镜像构建默认不走代理
- Node/npm/PyPI/apt 构建源默认指向国内镜像
- runtime 容器联网单独由 `TEAMOS_RUNTIME_HTTP_PROXY` 等变量控制

镜像化 runtime 默认：

- 持久真相源使用 `TEAMOS_DB_URL`
- 本地只保留 Docker volume 中的 runtime 临时状态；项目真相源继续在 `~/.teamos/workspace`
- 继续复用宿主机 `${HOME}/.codex` 和 `${HOME}/.openclaw`
- 不会隐式把镜像内 `/team-os` 快照当作默认 target 扫描；新部署会先空转等待 target 注册

## 日志与健康检查

```bash
cd ~/.teamos/runtime-config/default
make logs
```

Control Plane health：

```bash
curl -fsS http://127.0.0.1:${CONTROL_PLANE_PORT:-8787}/healthz
curl -fsS http://127.0.0.1:${CONTROL_PLANE_PORT:-8787}/v1/status
```

注册一个 improvement target：

```bash
curl -fsS -X POST http://127.0.0.1:${CONTROL_PLANE_PORT:-8787}/v1/improvement/targets \
  -H 'content-type: application/json' \
  -d '{
    "project_id": "demo",
    "target_id": "demo-team-os",
    "display_name": "Demo Team OS",
    "repo_url": "https://github.com/wangguanran/team-os.git",
    "repo_locator": "wangguanran/team-os",
    "workstream_id": "general"
  }'
```

OpenHands Agent Server health（若暴露到宿主）：

```bash
curl -fsS http://127.0.0.1:${OPENHANDS_AGENT_SERVER_PORT:-18000}/alive
```

## Makefile

- `make up` / `make down`
- 本地不再支持 `make build` / `make up-build`；如需新镜像，请等待 GitHub CI 发布后再 `make up`
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

在 `~/.teamos/runtime-config/default/.env` 中设置：

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
- `POST /v1/repo_improvement/run`（旧别名仍保留用于兼容）
- `GET /v1/repo_improvement/proposals`
- `POST /v1/repo_improvement/proposals/decide`

Default behavior:

- `control-plane` startup triggers one CrewAI repo-improvement run for the current `team-os` repo.
- `control-plane` also keeps a continuous repo-improvement loop running for the current repo.
- The repo-improvement run can also target another local repo via `repo_path`.
- Bug findings are materialized immediately.
- Feature findings become proposals, wait for user confirmation, and respect a 1 hour cooldown before materialization.
- Process optimizations collect telemetry for 24 hours before materialization.
- Findings are recorded into Team OS task ledgers and, when GitHub auth is available, mirrored to GitHub issues plus the configured GitHub Project panel.
