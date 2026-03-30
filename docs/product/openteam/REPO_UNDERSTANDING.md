# Repo Understanding

- repo: `/home/wangguanran/openteam/.worktrees/single-node-cutover`
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

`single-node local system` 是当前文档 contract。遗留的多节点脚本、Hub 资产或集群目录即使仍然存在，也不构成当前 operator 主路径。

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

## 已知迁移期矛盾

以下内容在代码层可能仍然存在，但不应继续出现在主产品文档里：

- 可选 Postgres DSN 支持
- 遗留 cluster / node / hub 相关脚本或目录
- 与旧运行形态相关的 DB、Redis、远程节点逻辑

如果这些遗留实现影响文档真实性，应在任务报告里单独列出，由代码所有者继续清理。
