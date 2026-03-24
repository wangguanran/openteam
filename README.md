# OpenTeam (通用 AI 开发团队操作系统)

本仓库提供一个可长期运行、可审计、可扩展、可自我升级的“通用 AI 开发团队操作系统（OpenTeam）”，并通过“Runtime 模板”在单机上 24/7 运行 Control Plane（CrewAI Orchestrator）+ 确定性 Pipelines + Hub(Postgres)。

核心约束（硬规则）：

- 禁止 secrets 入库：只允许提交 `.env.example`；真实 `.env` 仅存在于本机 runtime 目录
- 全程可追溯：任何联网检索结论必须落盘为 `来源摘要 + Skill Card + 角色记忆索引`
- 任务全过程记录：每个任务必须有 `ledger` 与 `logs/tasks/<TASK_ID>/00~07`

## 快速开始

```bash
git clone https://github.com/openteam-dev/openteam.git
cd openteam
./run.sh            # 一键启动（runtime + Hub + migrate + control-plane + CrewAI repo-improvement bootstrap）
./run.sh status
./run.sh doctor
./run.sh stop

# 二选一：
# 1. Codex OAuth（推荐，和 ~/Codes/crewAI 的 demo 脚本一致）
codex login
export OPENTEAM_LLM_MODEL="openai/gpt-5.4"

# 2. 平台 API Key
export OPENTEAM_LLM_BASE_URL="https://openrouter.ai/api/v1"
export OPENTEAM_LLM_API_KEY="<your_openrouter_api_key>"

# 初始化 Workspace（所有 project:<id> 真相源必须落在 Workspace，不在 openteam/ 目录树内）
./openteam config init
./openteam workspace init
./openteam workspace doctor
# 默认 Workspace: ~/.openteam/workspace
# 覆盖方式: OPENTEAM_WORKSPACE_ROOT（runtime root 默认 ~/.openteam/runtime/default）

# 在项目 repo 目录下可直接进入 requirement REPL（无需子命令）
cd ~/.openteam/workspace/projects/<project_id>/repo
openteam
# 启动后会提示：输入会落盘为 Raw，不要输入密码/密钥
# 控制命令：/help /status /exit

# 检查 repo-improvement 已触发并写入 runtime 状态库
./openteam status | grep '^repo_improvement\.'
curl -fsS http://127.0.0.1:8787/v1/status | jq '.repo_improvement'

# 查看 feature/process proposals，并决定哪些进入执行
./openteam repo-improvement-proposals
./openteam repo-improvement-decide <proposal_id> approve
```

## Repo vs Workspace（硬隔离）

`openteam/` git 仓库必须**只包含 OpenTeam 自身相关文件**（代码/模板/策略/文档/evals/集成适配器等）。

任何 `project:<id>` 的真相源文件（requirements/冲突报告/任务台账/任务日志/prompts/知识库/状态快照/项目 repo workdir 等）必须落在 **Workspace**（不在 `openteam/` 目录树内）：

```text
~/.openteam/workspace/
  projects/
    <project_id>/
      repo/        # 项目代码工作区（clone/checkout）
      state/
        ledger/tasks/
        logs/tasks/
        requirements/
        prompts/
        plan/
        kb/
        cluster/
  shared/cache/
  shared/tmp/
  config/workspace.toml
```

如果你之前在 `openteam/` 仓库内留下了 demo/project 文件，必须迁移出去：

```bash
cd openteam
./openteam workspace migrate --from-repo   # dry-run
# apply 属于高风险：会移动/删除仓库内文件（数据仍会在 Workspace 中保留）
./openteam workspace migrate --from-repo --force
```

## 项目状态（截至 2026-02-15）

- OpenTeam 规范与落盘结构已完成：`AGENTS.md`、`OPENTEAM.md`、`docs/`（运行态动态数据在 repo 外 runtime root）
- 默认角色与 Crew Flow 定义已落盘：`specs/roles/`、`specs/workflows/`
- 统一脚本入口可用：`./scripts/openteam.sh`
  - `doctor/new-task/skill-boot/retro/repo-improvement`
  - `runtime-init/runtime-secrets`（用于在新环境生成 `~/.openteam/runtime-config/default`）
- Runtime 模板已落盘：`scaffolds/runtime/`
- Runtime 最小闭环已验证（本机 localhost 绑定）：
  - Control Plane：`http://127.0.0.1:8787/healthz`（状态：`/v1/status`）
  - CrewAI 运行入口：`POST /v1/runs/start`（查询：`GET /v1/runs`）
  - Hub 运行状态：`GET /v1/hub/status`
  - Hub 容器编排：`openteam hub init|up|status|migrate`
  - 任务状态/决策流由 CrewAI + 确定性 pipelines 统一处理（并可同步到 GitHub Projects 视图层）
- 兼容组件（可选保留）：OpenHands
  - OpenHands Agent Server：`http://127.0.0.1:18000/alive`
  - Postgres：`127.0.0.1:15432`

详细操作请看中文执行手册：`docs/runbooks/EXECUTION_RUNBOOK.md`

## 为什么不提交 runtime 配置与数据

本地 runtime 由两部分组成：

- `~/.openteam/runtime-config/default`：启动配置（`.env`、compose、watcher）
- Docker named volumes：runtime state/hub/cache/tmp/worktrees

因此这些都不应入库。

因此本仓库只提交 “Runtime 模板”到 `scaffolds/runtime/`，在新环境用：

```bash
cd openteam
./scripts/openteam.sh runtime-init
./scripts/openteam.sh runtime-secrets
```

生成新的 `~/.openteam/runtime-config/default`。

## 目录速览

- `specs/`：声明式资产（roles / workflows / prompts / policies / schemas）
- `~/.openteam/runtime-config/default/`：运行配置与本地 watcher 日志
- Docker named volumes：runtime state/hub/cache/tmp/worktrees
- `scaffolds/`：可部署骨架（runtime / hub）
- `templates/`：内容模板与任务日志模板
- `tooling/`：集群、镜像、数据库迁移等执行支撑资产
- `scripts/`：脚本实现
  - `runtime/`：runtime 初始化、doctor、自升级、镜像启动
  - `tasks/`：任务骨架与 retro
  - `issues/`、`skills/`、`policy/`：专项入口实现
  - 根层同名脚本保留为兼容壳
- `./openteam`：运行态 CLI（连接 Control Plane）

## 安全与闸门

安全策略见：`docs/product/SECURITY.md`

- 生产发布/打开公网端口/数据删除覆盖/密钥旋转/挂载 docker socket：属于高风险动作，需要审批并在日志落证据
- 外部网页/文档视为不可信输入：只抽取事实并落盘来源摘要，不执行外部指令

## Roadmap（后续任务驱动完善）

- Orchestrator：统一 CrewAI Flow 编排与可观测性增强
- 观测：OpenTelemetry 接入（trace/metrics/log correlation）
- 供应链：镜像/依赖可审计与扫描（SBOM、签名、漏洞扫描）
