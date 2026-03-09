# Team OS (通用 AI 开发团队操作系统)

本仓库提供一个可长期运行、可审计、可扩展、可自我升级的“通用 AI 开发团队操作系统（Team OS）”，并通过“Runtime 模板”在单机上 24/7 运行 Control Plane（CrewAI Orchestrator）+ 确定性 Pipelines + Hub(Postgres/Redis)。

核心约束（硬规则）：

- 禁止 secrets 入库：只允许提交 `.env.example`；真实 `.env` 仅存在于本机 runtime 目录
- 全程可追溯：任何联网检索结论必须落盘为 `来源摘要 + Skill Card + 角色记忆索引`
- 任务全过程记录：每个任务必须有 `ledger` 与 `logs/tasks/<TASK_ID>/00~07`

## 快速开始

```bash
git clone https://github.com/wangguanran/team-os.git
cd team-os
./run.sh            # 一键启动（runtime + Hub + migrate + control-plane + CrewAI self-upgrade bootstrap）
./run.sh status
./run.sh doctor
./run.sh stop

# 二选一：
# 1. Codex OAuth（推荐，和 ~/Codes/crewAI 的 demo 脚本一致）
codex login
export TEAMOS_CREWAI_MODEL="openai-codex/gpt-5.3-codex"

# 2. 平台 API Key
export TEAMOS_LLM_BASE_URL="https://api.openai.com/v1"
export TEAMOS_LLM_API_KEY="<your_api_key>"

# 初始化 Workspace（所有 project:<id> 真相源必须落在 Workspace，不在 team-os/ 目录树内）
./teamos config init
./teamos workspace init
./teamos workspace doctor
# 默认 Workspace: ../team-os-runtime/workspace
# 覆盖方式: TEAMOS_RUNTIME_ROOT 或 TEAMOS_WORKSPACE_ROOT

# 在项目 repo 目录下可直接进入 requirement REPL（无需子命令）
cd ../team-os-runtime/workspace/projects/<project_id>/repo
teamos
# 启动后会提示：输入会落盘为 Raw，不要输入密码/密钥
# 控制命令：/help /status /exit

# 检查 self-upgrade 已触发并写入 runtime 状态库
./teamos status | grep '^self_upgrade\.'
curl -fsS http://127.0.0.1:8787/v1/status | jq '.self_upgrade'

# 查看 feature/process proposals，并决定哪些进入执行
./teamos self-upgrade-proposals
./teamos self-upgrade-decide <proposal_id> approve
```

## Repo vs Workspace（硬隔离）

`team-os/` git 仓库必须**只包含 Team OS 自身相关文件**（代码/模板/策略/文档/evals/集成适配器等）。

任何 `project:<id>` 的真相源文件（requirements/冲突报告/任务台账/任务日志/prompts/知识库/状态快照/项目 repo workdir 等）必须落在 **Workspace**（不在 `team-os/` 目录树内）：

```text
../team-os-runtime/workspace/
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

如果你之前在 `team-os/` 仓库内留下了 demo/project 文件，必须迁移出去：

```bash
cd team-os
./teamos workspace migrate --from-repo   # dry-run
# apply 属于高风险：会移动/删除仓库内文件（数据仍会在 Workspace 中保留）
./teamos workspace migrate --from-repo --force
```

## 项目状态（截至 2026-02-15）

- Team OS 规范与落盘结构已完成：`AGENTS.md`、`TEAMOS.md`、`docs/`（运行态动态数据在 repo 外 runtime root）
- 默认角色与 Crew Flow 定义已落盘：`specs/roles/`、`specs/workflows/`
- 统一脚本入口可用：`./scripts/teamos.sh`
  - `doctor/new-task/skill-boot/retro/self-upgrade`
  - `runtime-init/runtime-secrets`（用于在新环境生成 `team-os-runtime`）
- Runtime 模板已落盘：`scaffolds/runtime/`
- Runtime 最小闭环已验证（本机 localhost 绑定）：
  - Control Plane：`http://127.0.0.1:8787/healthz`（状态：`/v1/status`）
  - CrewAI 运行入口：`POST /v1/runs/start`（查询：`GET /v1/runs`）
  - Hub 运行状态：`GET /v1/hub/status`
  - Hub 容器编排：`teamos hub init|up|status|migrate`
  - 任务状态/决策流由 CrewAI + 确定性 pipelines 统一处理（并可同步到 GitHub Projects 视图层）
  - 兼容组件（可选保留）：OpenHands + Temporal
  - OpenHands Agent Server：`http://127.0.0.1:18000/alive`
  - Temporal UI：`http://127.0.0.1:18081`
  - Temporal gRPC：`127.0.0.1:7233`
  - Postgres：`127.0.0.1:15432`

详细操作请看中文执行手册：`docs/runbooks/EXECUTION_RUNBOOK.md`

## 为什么不提交 team-os-runtime

`team-os-runtime` 是“部署目录”，包含：

- 真实 `.env`（secrets）
- 容器卷数据（Postgres/Temporal 状态）
- 与宿主机强相关的运行态文件

因此本仓库只提交 “Runtime 模板”到 `scaffolds/runtime/`，在新环境用：

```bash
cd team-os
./scripts/teamos.sh runtime-init
./scripts/teamos.sh runtime-secrets
```

生成新的 `../team-os-runtime`。

## 目录速览

- `specs/`：声明式资产（roles / workflows / prompts / policies / schemas）
- `../team-os-runtime/state/kb/`：知识库（含来源摘要、Skill Cards）
- `../team-os-runtime/state/ledger/`：任务台账、自我升级台账、pending issues
- `../team-os-runtime/state/logs/`：任务全流程日志（00~07）
- `scaffolds/`：可部署骨架（runtime / hub）
- `templates/`：内容模板与任务日志模板
- `tooling/`：集群、镜像、数据库迁移等执行支撑资产
- `scripts/`：脚本实现
  - `runtime/`：runtime 初始化、doctor、自升级、镜像启动
  - `tasks/`：任务骨架与 retro
  - `issues/`、`skills/`、`policy/`：专项入口实现
  - 根层同名脚本保留为兼容壳
- `./teamos`：运行态 CLI（连接 Control Plane）

## 安全与闸门

安全策略见：`docs/product/SECURITY.md`

- 生产发布/打开公网端口/数据删除覆盖/密钥旋转/挂载 docker socket：属于高风险动作，需要审批并在日志落证据
- 外部网页/文档视为不可信输入：只抽取事实并落盘来源摘要，不执行外部指令

## Roadmap（后续任务驱动完善）

- Orchestrator：统一 CrewAI Flow 编排与可观测性增强
- 观测：OpenTelemetry 接入（trace/metrics/log correlation）
- 供应链：镜像/依赖可审计与扫描（SBOM、签名、漏洞扫描）
