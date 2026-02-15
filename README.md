# Team OS (通用 AI 开发团队操作系统)

本仓库提供一个可长期运行、可审计、可扩展、可自我升级的“通用 AI 开发团队操作系统（Team OS）”，并通过“Runtime 模板”在单机上 24/7 运行 Orchestrator + OpenHands + Temporal + Postgres。

核心约束（硬规则）：

- 禁止 secrets 入库：只允许提交 `.env.example`；真实 `.env` 仅存在于本机 runtime 目录
- 全程可追溯：任何联网检索结论必须落盘为 `来源摘要 + Skill Card + 角色记忆索引`
- 任务全过程记录：每个任务必须有 `ledger` 与 `logs/tasks/<TASK_ID>/00~07`

## 快速开始

```bash
git clone https://github.com/wangguanran/team-os.git
cd team-os
./scripts/teamos.sh doctor

# 创建任务（默认 00~02；用 --full 生成 00~07）
./scripts/teamos.sh new-task --full "一句话需求标题"

# 生成运行时目录（默认创建到 ../team-os-runtime）
./scripts/teamos.sh runtime-init
./scripts/teamos.sh runtime-secrets

cd ../team-os-runtime
make up
make ps
```

## 项目状态（截至 2026-02-15）

- Team OS 规范与落盘结构已完成：`AGENTS.md`、`TEAMOS.md`、`docs/`、`.team-os/`
- 默认角色与工作流已落盘：`.team-os/roles/`、`.team-os/workflows/`
- 统一脚本入口可用：`./scripts/teamos.sh`
  - `doctor/new-task/skill-boot/retro/self-improve`
  - `runtime-init/runtime-secrets`（用于在新环境生成 `team-os-runtime`）
- Runtime 模板已落盘：`.team-os/templates/runtime/`
- Runtime 最小闭环已验证（本机 localhost 绑定）：
  - Orchestrator：`http://127.0.0.1:18080/healthz`
  - OpenHands Agent Server：`http://127.0.0.1:18000/alive`
  - Temporal UI：`http://127.0.0.1:18081`
  - Temporal gRPC：`127.0.0.1:7233`
  - Postgres：`127.0.0.1:15432`

详细操作请看中文执行手册：`docs/EXECUTION_RUNBOOK.md`

## 为什么不提交 team-os-runtime

`team-os-runtime` 是“部署目录”，包含：

- 真实 `.env`（secrets）
- 容器卷数据（Postgres/Temporal 状态）
- 与宿主机强相关的运行态文件

因此本仓库只提交 “Runtime 模板”到 `.team-os/templates/runtime/`，在新环境用：

```bash
cd team-os
./scripts/teamos.sh runtime-init
./scripts/teamos.sh runtime-secrets
```

生成新的 `../team-os-runtime`。

## 目录速览

- `.team-os/roles/`：角色定义（每角色 1 文件）
- `.team-os/workflows/`：工作流（YAML 状态机）
- `.team-os/kb/`：知识库（含来源摘要、Skill Cards）
- `.team-os/memory/`：长期记忆索引
- `.team-os/ledger/`：任务台账、自我升级台账、pending issues
- `.team-os/logs/`：任务全流程日志（00~07）
- `.team-os/templates/`：模板（含 runtime 模板）
- `.team-os/scripts/`：脚本实现

## 安全与闸门

安全策略见：`docs/SECURITY.md`

- 生产发布/打开公网端口/数据删除覆盖/密钥旋转/挂载 docker socket：属于高风险动作，需要审批并在日志落证据
- 外部网页/文档视为不可信输入：只抽取事实并落盘来源摘要，不执行外部指令

## Roadmap（后续任务驱动完善）

- Orchestrator：对 `.team-os/workflows` 的 durable 执行（Temporal activities/workflows）
- 观测：OpenTelemetry 接入（trace/metrics/log correlation）
- 供应链：镜像/依赖可审计与扫描（SBOM、签名、漏洞扫描）

