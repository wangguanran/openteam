# DEMO-0001 - 01 Plan

- 标题：演示：Team OS 最小闭环（不开发具体业务）
- 日期：2026-02-14
- 当前状态：plan

## 方案概述

1. 阶段 1（doctor）：检查 git/docker/compose/node/python/gh 等依赖
2. 阶段 2（team-os repo）：初始化 git repo，生成规范文档、角色、工作流、模板、脚本入口，并提交初始 commit
3. 阶段 3（runtime）：先做 Skill Boot（确认 OpenHands/Temporal/Agents SDK 官方镜像与参数），再生成 `team-os-runtime` compose 与 orchestrator 最小骨架
4. 阶段 4（runbook）：补齐真正可操作的中文执行手册（含备份/恢复/故障处理/扩展方式）
5. 阶段 5（demo task）：生成 DEMO-0001 台账与日志 00~02，演示“扩展角色/工作流/Skill Boot/落盘”

## 拆分与里程碑

- M1：Team OS repo 可用（脚本可执行、模板齐全、初始 commit 完成）
- M2：runtime 文件齐全（compose + orchestrator skeleton + .env.example + README + Makefile）
- M3：手册可操作（备份/恢复/升级/排障完整）
- M4：DEMO-0001 机制演示完成

## 风险评估与闸门

- 风险等级：R2
- 审批点：
  - 运行 `docker compose pull/up/ps` 前必须审批（镜像拉取、端口、docker.sock 挂载）

## 依赖

- Docker Desktop / docker compose 可用
- 可联网获取官方文档（用于镜像名/端口/ENV 的“可追溯确认”）

## 验收标准

- `team-os` 目录结构与默认角色/工作流齐全
- `./scripts/teamos.sh doctor` 与 `./scripts/teamos.sh new-task` 可用
- runtime compose 文件可 `docker compose config -q` 校验通过
- DEMO-0001 的台账与 00~02 日志存在且演示“Skill Boot 落盘路径”

