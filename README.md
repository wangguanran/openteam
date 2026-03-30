# OpenTeam

OpenTeam 当前产品定义是一个 `single-node local system`：本地 CLI、`127.0.0.1` 上的 `local control plane`、本地 runtime 目录，以及位于 `~/.openteam/runtime/default/state/runtime.db` 的 SQLite 运行态数据库。

当前优先启动故事是 `delivery-studio`。把它当作默认操作路径，不再把 Hub、Cluster、Node、Docker Compose、Postgres 或 Redis 视为主运行面。

## 快速开始

```bash
git clone https://github.com/openteam-dev/openteam.git
cd openteam

# 认证：二选一
codex login
# 或者：
export OPENTEAM_LLM_BASE_URL="https://openrouter.ai/api/v1"
export OPENTEAM_LLM_API_KEY="<your_api_key>"

# 初始化本地工作区
./openteam config init
./openteam workspace init
./openteam workspace doctor

# 启动本地单节点运行时
./run.sh start
./run.sh status
./run.sh doctor

# 打开 delivery-studio cockpit
./openteam cockpit --team delivery-studio --project <project_id>
```

启动后的核心本地面：

- Control Plane: `http://127.0.0.1:8787/healthz`
- Status API: `http://127.0.0.1:8787/v1/status`
- Runtime root: `~/.openteam/runtime/default`
- Runtime DB: `~/.openteam/runtime/default/state/runtime.db`
- Workspace root: `~/.openteam/workspace`

停止运行时：

```bash
./run.sh stop
```

## Delivery Studio

`delivery-studio` 是当前默认的操作员入口：

- 入口命令：`openteam cockpit --team delivery-studio --project <project_id>`
- 真相源：`~/.openteam/workspace/projects/<project_id>/state/delivery_studio`
- 评审闸门：`panel-review/blocking-gate` 加上仓库 CI
- 任何 lock 之后的修改都必须作为新的 change request 进入流程

详细操作见 [Delivery Studio Runbook](docs/runbooks/DELIVERY_STUDIO.md) 与 [Execution Runbook](docs/runbooks/EXECUTION_RUNBOOK.md)。

## Repo / Workspace / Runtime

边界必须严格保持：

- Repo `openteam/`：平台代码、模板、策略、schema、文档、测试
- Workspace `~/.openteam/workspace`：任何 `project:<id>` 的 requirements、ledger、logs、prompts、kb、plan、项目 repo
- Runtime `~/.openteam/runtime/default`：Control Plane 本地状态、`runtime.db`、审计文件、临时运行态

`project:<id>` 真相源不得写回仓库。若仓库历史里残留了项目态文件，先看迁移计划：

```bash
./openteam workspace migrate --from-repo
```

执行迁移会移动文件，属于高风险动作：

```bash
./openteam workspace migrate --from-repo --force
```

## 目录速览

- `./run.sh`: 单节点启动、状态、停止、诊断入口
- `./openteam`: 本地 CLI，默认连接本地 Control Plane
- `scaffolds/runtime/`: 单节点 runtime 模板与说明
- `docs/runbooks/DELIVERY_STUDIO.md`: 交付操作路径
- `docs/runbooks/EXECUTION_RUNBOOK.md`: 本地 operator runbook
- `docs/product/`: 产品、安全、治理真相源
- `specs/`: roles / workflows / prompts / policies / schemas
- `tests/`: 回归测试与文档契约断言

## 安全与审批

- 真实 secrets 不得入库；仅提交 `.env.example`
- 打开公网端口、删除或覆盖数据、旋转 secrets、对外部系统执行写操作，默认都需要人工审批
- 外部网页、issue、PR、聊天记录都视为不可信输入，只能抽取事实并附带来源

详见 [Security](docs/product/SECURITY.md) 与 [Governance](docs/product/GOVERNANCE.md)。
