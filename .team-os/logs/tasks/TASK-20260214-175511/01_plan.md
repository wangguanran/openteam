# TASK-20260214-175511 - 01 Plan

- 标题：Bootstrap runtime (team-os-runtime bring-up)
- 日期：2026-02-15
- 当前状态：plan -> todo

## 方案概述

- Runtime（`team-os-runtime`）采用 Docker Compose：
  - `postgres:16`：Temporal 持久化存储（卷 `postgres_data`）
  - `temporalio/server` + `temporalio/ui`：durable workflow
  - `ghcr.io/openhands/agent-server:latest-python`：隔离执行平面（挂载 docker socket，默认仅本机端口）
  - `orchestrator`：Python + FastAPI + OpenAI Agents SDK 最小骨架（`/healthz`）

核心策略：
- Secrets：仅写入 `team-os-runtime/.env`（不入 git）；`.env.example` 仅列变量名。
- 观测：先用 `docker compose logs` + health endpoints；后续预留 OTel。

## 拆分与里程碑

- M1：Runtime 基础服务启动成功（postgres/temporal/ui/orchestrator）
- M2：OpenHands 镜像可拉取且服务可运行（含 healthcheck）
- M3：部署验证证据落盘到 `docs/EXECUTION_RUNBOOK.md`
- M4：补齐任务日志 03~07 并完成 Retro + Self-Improve 草稿

## 风险评估与闸门

- 风险等级：R2
- 审批点：
  - 若需要 `docker compose down -v`（清卷）才能修复：属于数据覆盖/删除动作
  - 若需要对外暴露端口（去掉 `127.0.0.1` 绑定）：属于公网暴露动作
  - secrets 旋转：需要明确影响与回滚

## 依赖

- 本机依赖：`docker`、`docker compose`、`python3`、（可选）`gh`
- 外部依赖：Docker Hub / GHCR 镜像拉取可用

## 验收标准

- `docker compose ps` 显示：
  - `postgres/temporal/temporal-ui/openhands-agent-server/orchestrator` 为 `Up` 且关键服务为 `healthy`
- 健康检查通过：
  - `curl http://127.0.0.1:18080/healthz` 返回 `status=ok`
  - `curl http://127.0.0.1:18000/alive` 返回 `status=ok`
- 文档更新：
  - `docs/EXECUTION_RUNBOOK.md` 的“部署验证”章节包含实际命令与结果摘要（无 secrets）
