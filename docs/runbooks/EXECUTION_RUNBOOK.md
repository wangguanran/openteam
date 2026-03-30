# 执行手册

本文是 OpenTeam 当前的 operator runbook。默认场景只有一个：在本机运行一个单节点 OpenTeam，并以 `delivery-studio` 作为优先启动路径。

## 1. 当前运行 contract

- 单节点本地系统，不讲 Hub / Cluster / Node
- 本地 CLI：`./openteam`
- 本地启动器：`./run.sh`
- 本地 Control Plane：`http://127.0.0.1:8787`
- 本地 runtime root：`~/.openteam/runtime/default`
- 本地运行态数据库：`~/.openteam/runtime/default/state/runtime.db`
- 项目真相源：`~/.openteam/workspace/projects/<project_id>/...`

## 2. 前置准备

- `git`
- `python3`
- `codex login`，或可用的模型 API 环境变量
- 可选：`gh auth login`，仅当你要使用 GitHub Projects 视图层同步

## 3. 初始化本地环境

```bash
cd openteam
./openteam config init
./openteam workspace init
./openteam workspace doctor
```

默认路径：

- Workspace: `~/.openteam/workspace`
- Runtime: `~/.openteam/runtime/default`

## 4. 启动 OpenTeam

```bash
cd openteam
./run.sh start
./run.sh status
./run.sh doctor
```

健康检查：

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/v1/status
```

如需停止或重启：

```bash
./run.sh stop
./run.sh restart
```

## 5. Delivery Studio 操作路径

这是当前默认的 operator 入口：

```bash
./openteam cockpit --team delivery-studio --project <project_id>
```

推荐的日常流程：

1. 打开 cockpit
2. 创建或导入需求
3. 等待进入审批或 review gate
4. 生成计划并开始执行
5. 在 `panel-review/blocking-gate` 和 CI 通过后再合并

交付真相源位于：

```text
~/.openteam/workspace/projects/<project_id>/state/delivery_studio
```

## 6. 运行态检查

查看本地 CLI 状态：

```bash
./openteam status
./openteam doctor
```

如果本机安装了 `sqlite3`，可以直接查看运行态数据库：

```bash
sqlite3 ~/.openteam/runtime/default/state/runtime.db '.tables'
```

常看的目录：

```text
~/.openteam/runtime/default/state/ledger/tasks/
~/.openteam/runtime/default/state/logs/tasks/
~/.openteam/runtime/default/state/audits/
~/.openteam/workspace/projects/<project_id>/state/
```

## 7. Repo / Workspace / Runtime 边界

- Repo 只放平台代码、模板、文档、测试
- Workspace 只放 `project:<id>` 真相源
- Runtime 只放本地控制面状态、`runtime.db`、OpenTeam 自身 ledger/logs/audits

若发现项目态文件混进仓库，先看迁移计划：

```bash
./openteam workspace migrate --from-repo
```

执行迁移会移动文件，属于高风险动作：

```bash
./openteam workspace migrate --from-repo --force
```

## 8. GitHub Projects 视图层

GitHub Projects 只是视图层，不是真相源。需要时再打开：

```bash
export GITHUB_TOKEN="$(gh auth token -h github.com)"
./openteam panel show --project <project_id>
./openteam panel sync --project <project_id> --dry-run
```

远程写入默认需要审批。

## 9. 故障排查

- `./run.sh status` 失败：先执行 `./run.sh doctor`
- `workspace doctor` 失败：先修复 Workspace 路径与目录结构
- `cockpit` 打不开：确认 `./run.sh start` 后 `curl /healthz` 与 `curl /v1/status` 正常
- `panel sync` 失败：确认 `gh auth status` 正常，且映射文件已配置
- `runtime.db` 不存在：重新执行 `./run.sh start`，然后检查 `~/.openteam/runtime/default/state/`

## 10. 不在本手册覆盖的内容

以下内容不再属于当前 operator 主路径：

- Hub 初始化、备份、恢复、配置分发
- Cluster 选主、节点心跳、远程租约
- Node bootstrap、远程机器引导

如果代码中仍有这些遗留入口，把它们视为迁移期残留，不要把它们当作当前产品文档 contract。
