# OPENTEAM.md

> 目标：把 OpenTeam 定义成一个 `single-node local system`。主运行面只有本地 CLI、本地 control plane、本地 runtime 目录，以及位于 `~/.openteam/runtime/default/state/runtime.db` 的 SQLite 运行态数据库。

## 1. 当前产品定义

- mode: `single-node local system`
- primary story: `delivery-studio`
- entrypoints:
  - `./run.sh [start|status|stop|restart|doctor]`
  - `./openteam`
  - `./openteam cockpit --team delivery-studio --project <project_id>`

当前 contract 只覆盖本机 CLI、本机 control plane、本地 runtime 与本地 workspace，不依赖额外的外部服务编排层。

## 2. 组件边界

### 2.1 Control Plane

- 本地 control plane 监听 `127.0.0.1:8787`
- 提供可审计的本地查询和注入接口
- 启动入口由 `scripts/bootstrap_and_run.py` 和 `run.sh` 统一驱动

### 2.2 Runtime

- runtime root: `~/.openteam/runtime/default`
- runtime DB: `~/.openteam/runtime/default/state/runtime.db`
- audit: `~/.openteam/runtime/default/state/audit/`
- logs / ledger / local state 都在 runtime root 内

### 2.3 Workspace

- workspace root: `~/.openteam/workspace`
- 项目真相源必须留在：
  - `~/.openteam/workspace/projects/<project_id>/state/requirements/`
  - `~/.openteam/workspace/projects/<project_id>/state/delivery_studio/`
  - `~/.openteam/workspace/projects/<project_id>/state/ledger/`
  - `~/.openteam/workspace/projects/<project_id>/state/logs/`
  - `~/.openteam/workspace/projects/<project_id>/repo/`

Repo 只存平台代码、docs、schemas、specs、tests，不存项目真相源和运行态。

## 3. Delivery Studio First

当前优先操作路径：

```bash
./run.sh start
./openteam cockpit --team delivery-studio --project <project_id>
```

`delivery-studio` 是默认团队故事。需求讨论、批准、review、test completeness、CI gate 都应围绕它展开。

## 4. 任务与证据

- 任务必须有 ledger、logs、阶段化痕迹
- 批准前不编码
- 文档、CHANGELOG、design package、contract baseline、master plan 是开发前输入
- reviewer 和测试/CI 是硬闸门，不是建议面板

## 5. 风险分级

- R0: 文档、模板、纯说明
- R1: 本地可回滚开发与测试
- R2: 外部写操作、网络暴露、自动化脚本影响面扩大
- R3: 生产发布、不可逆迁移、真实 secrets、数据覆盖

R2/R3 必须有人类审批并留下证据。

## 6. 常用状态面

```bash
./run.sh status
./run.sh doctor
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/v1/status
```

## 7. 一句话原则

OpenTeam 现在首先是一个可启动、可审计、可恢复的单机团队操作系统，而不是一个多节点基础设施仓库。
