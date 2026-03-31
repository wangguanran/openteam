# OpenTeam Runtime Scaffold

本目录描述的是 OpenTeam 当前的单节点本地运行时模板，不是 Hub/Cluster 部署模板。

运行时 contract：

- 本地 CLI：`./openteam`
- 本地启动器：`./run.sh [start|status|stop|restart|doctor]`
- 本地 Control Plane：`127.0.0.1:8787`
- 本地 runtime root：`~/.openteam/runtime/default`
- 本地 SQLite：`~/.openteam/runtime/default/state/runtime.db`
- 本地审计目录：`~/.openteam/runtime/default/state/audit/`
- 项目真相源：`~/.openteam/workspace/projects/<project_id>/...`

## 启动顺序

先在仓库根目录初始化本地环境：

```bash
./openteam config init
./openteam workspace init
./openteam workspace doctor
```

启动单节点运行时：

```bash
./run.sh start
./run.sh status
```

检查健康状态：

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/v1/status
```

当前 runtime 建议统一通过本机 `LiteLLM Proxy` 接模型。默认示例配置为：

```bash
export OPENTEAM_LLM_GATEWAY=litellm_proxy
export OPENTEAM_LLM_BASE_URL=http://127.0.0.1:4000/v1
export OPENTEAM_LLM_MODEL=openai/gpt-5.4
export OPENTEAM_CREWAI_REASONING_EFFORT=xhigh
```

Team workflow 仍然可以为不同 agent 指定不同模型，例如 `openai/gpt-5.4`、`anthropic/claude-opus-4.6`；但 provider 路由统一由全局 LiteLLM 层负责，不再在 team spec 里硬编码 `openrouter/*`。

`./run.sh start` 在 `OPENTEAM_LLM_GATEWAY=litellm_proxy` 时会先生成本机 LiteLLM 配置并尝试拉起 proxy，再启动 control-plane。当前生成的配置落在：

```bash
~/.openteam/runtime/default/state/openteam/litellm_config.yaml
```

LiteLLM proxy 会扫描 team workflow 里声明的模型，并读取上游 provider 凭据，例如：

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export OPENROUTER_API_KEY=...
```

Repo-improvement 的 background workflow loop 不再由 `OPENTEAM_TEAM_WORKFLOW_*` 环境变量控制；具体的 `bug-finding`、`feature-discussion`、`coding` 等 workflow 配置都定义在 `scaffolds/runtime/orchestrator/app/teams/repo_improvement/specs/workflows/*.yaml`。Runtime 侧只保留全局开关 `OPENTEAM_RUNTIME_WORKFLOW_LOOPS_ENABLED`。

Team 目录现在只保留配置；workflow 里的 `kind: skill` 节点会通过 `scaffolds/runtime/orchestrator/app/skill_library/specs/*.yaml` 描述的技能库执行，具体实现位于通用 runtime skill/domain 模块，而不是 team 目录脚本。
停止或重启：

```bash
./run.sh stop
./run.sh restart
```

## Delivery Studio First

当前优先操作路径是：

```bash
./openteam cockpit --team delivery-studio --project <project_id>
```

使用这个入口来处理需求、审批、计划、执行和 review gate。`delivery-studio` 对应的项目真相源在 Workspace：

```text
~/.openteam/workspace/projects/<project_id>/state/delivery_studio
```

## Runtime Layout

```text
~/.openteam/
  runtime/
    default/
      state/
        runtime.db
        ledger/
        logs/
        audit/
  workspace/
    projects/
      <project_id>/
        repo/
        state/
          delivery_studio/
          ledger/
          logs/
          requirements/
          prompts/
          kb/
          plan/
```

说明：

- `runtime.db` 是本地 SQLite 运行态数据库
- `ledger/`、`logs/`、`audit/` 存放 OpenTeam 自身的本地任务与审计痕迹
- 任何 `project:<id>` 真相源都必须留在 Workspace，不写回仓库

## 常用命令

```bash
./openteam status
./openteam doctor
./openteam panel show --project <project_id>
./openteam panel sync --project <project_id> --dry-run
./openteam cockpit --team delivery-studio --project <project_id>
```

## Secrets

- 本目录只提交模板与说明，不提交真实 `.env`
- 真实凭据只放本机环境变量、系统钥匙串或本地 runtime 配置
- 如需 GitHub Projects 视图层同步，先在本机执行 `gh auth login`，再通过环境变量注入 token

## Known Limits

- 这里描述的是单节点主路径，不为多节点、集群选主、远程节点引导背书
- 仓库里仍可能存在迁移期代码或脚本，但不属于当前 operator 文档 contract
