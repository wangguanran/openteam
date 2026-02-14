# TASK-20260214-175511 - 00 Intake

- 标题：Bootstrap runtime (team-os-runtime bring-up)
- 日期：2026-02-15
- 当前状态：intake -> plan

## 一句话需求

- 在 `team-os-runtime` 一键拉起 Postgres + Temporal(+UI) + OpenHands Agent Server + Orchestrator，并用健康检查证明可长期运行；同时满足“无 secrets 入库、可追溯、可复现”。

## 目标/非目标

- 目标：
  - `docker compose` 下所有 runtime 服务均可启动并稳定运行（`healthy` 或可解释的 `exit 0` one-shot）。
  - 生成/维护 `.env` secrets（仅本地文件），并提供 `.env.example`。
  - 关键验证命令与结果摘要落盘到 `team-os/docs/EXECUTION_RUNBOOK.md`。
  - 为本任务生成 ledger + 全流程任务日志（00~07）。
- 非目标：
  - 不接入真实业务、不进行生产发布。
  - 不在本任务中实现完整的 Orchestrator 工作流执行（仅最小骨架与健康检查）。

## 约束与闸门

- 风险等级：R2
- 需要审批的动作（如有）：
  - 打开公网端口（本任务默认只绑定 `127.0.0.1`）。
  - 任何数据删除/覆盖（例如 `docker compose down -v`）。
  - 任何 secrets 旋转（会影响已运行服务）。

## 澄清问题 (必须回答)

- Runtime 部署目标环境：本机 macOS + Docker Desktop（已确认）。
- 是否需要 OpenHands 暴露到公网：否（仅本机 localhost）。
- 是否需要立刻接入 OpenAI Key：否（保留接口，健康检查不依赖 Key）。

## 需要哪些角色/工作流扩展

- 角色：
  - Release-Ops：Runtime bring-up、compose 调整、健康检查与风险控制
  - Developer-AI：Orchestrator 最小骨架与依赖管理
  - Reviewer：安全与闸门复核（docker socket、端口暴露、secrets）
  - Process-Guardian：记录、复盘、自我升级条目
- 工作流：
  - Delivery（实现/测试/发布/观测/关闭）

## 产物清单 (本任务必须落盘的文件路径)

- 任务台账：`.team-os/ledger/tasks/TASK-20260214-175511.yaml`
- 任务日志：`.team-os/logs/tasks/TASK-20260214-175511/00~07_*.md`
- 执行手册更新：`docs/EXECUTION_RUNBOOK.md`
