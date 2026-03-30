# Repo Understanding

- repo: `openteam/`（active repository checkout）
- mode: `single-node local system`
- primary operator story: `delivery-studio`

## 总体架构

OpenTeam 当前应被理解为一个本地单节点系统：

- 本地 CLI：`./openteam`
- 本地启动器：`./run.sh`
- 本地 Control Plane：`scaffolds/runtime/orchestrator/app/main.py`
- 本地 runtime root：`~/.openteam/runtime/default`
- 本地运行态数据库：`~/.openteam/runtime/default/state/runtime.db`
- 项目真相源：`~/.openteam/workspace/projects/<project_id>/...`

`single-node local system` 是当前文档 contract。多节点、Hub、远程节点、Docker runtime 模板不再属于当前 operator 主路径。

## 模块边界与职责

- CLI：`openteam`
- 单节点 bootstrap：`run.sh` 与 `scripts/bootstrap_and_run.py`
- Control Plane：`scaffolds/runtime/orchestrator/app/main.py`
- Runtime state helpers：`scaffolds/runtime/orchestrator/app/state_store.py`
- Runtime DB facade：`scaffolds/runtime/orchestrator/app/runtime_db.py`
- Requirements / prompts / panel 等决定性入口：`scripts/`
- 产品与 operator 文档：`docs/product/`、`docs/runbooks/`

## 当前 operator 路径

优先入口：

```bash
./run.sh start
./openteam cockpit --team delivery-studio --project <project_id>
```

常看状态面：

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/v1/status
sqlite3 ~/.openteam/runtime/default/state/runtime.db '.tables'
```

## 目录理解

- Repo：平台代码、模板、文档、测试
- Workspace：项目真相源
- Runtime：本地运行态、审计、`runtime.db`

关键目录：

```text
README.md
run.sh
openteam
scaffolds/runtime/
docs/runbooks/DELIVERY_STUDIO.md
docs/runbooks/EXECUTION_RUNBOOK.md
docs/product/GOVERNANCE.md
docs/product/SECURITY.md
scripts/
tests/
```

## 当前清理方向

当前仓库只为单机运行背书：

- 本地 CLI
- 本地 control plane
- 本地 `runtime.db`
- workspace 中的项目真相源

任何与 Hub、集群选主、远程节点引导、容器化 runtime 部署相关的遗留内容，都应视为待删除或待重设计资产，而不是默认能力。
