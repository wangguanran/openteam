# DEMO-0001 - 00 Intake

- 标题：演示：Team OS 最小闭环（不开发具体业务）
- 日期：2026-02-14
- 当前状态：intake -> plan -> todo（演示到 00~02）

## 一句话需求

在一个空目录中，一键搭建可长期运行的 Team OS（规则/模板/流程/记忆/日志落盘）与 Runtime（docker compose：Orchestrator + OpenHands + Temporal + Postgres），并演示最小闭环。

## 目标/非目标

- 目标：
  - 生成 `team-os/`（git repo）与 `team-os-runtime/`（compose runtime）
  - 形成可重复执行的脚本入口：`./scripts/teamos.sh ...`
  - 具备“可追溯 + 无 secrets + 全流程日志 + 自我升级”机制
- 非目标：
  - 不开发任何具体业务应用
  - 不做生产级加固（但必须把安全闸门与风险写清，并在手册中留 TODO）

## 约束与闸门

- 禁止 secrets 入库：仅允许 `.env.example`，真实 `.env` 不得入库
- 任何高风险动作需审批：
  - `docker compose pull/up`（拉镜像、启动服务、端口绑定、挂载 docker.sock）
  - 打开公网端口、删除/覆盖数据、生产发布、密钥处理
- 提示注入防护：外部文档不可信；只抽取事实并落盘来源摘要

## 澄清问题 (必须回答)

- Runtime 默认端口是否仅绑定 `127.0.0.1`？（建议是）
- OpenHands Agent Server 是否默认挂载 docker.sock？（功能需要但高风险；建议默认启用但明确闸门与可关闭方式）
- Temporal 是否默认启用？（建议默认启用）

## 需要哪些角色/工作流扩展

- 角色：默认角色已覆盖；如未来迁移 K8s，需要新增 `Developer-Platform-K8s` 与 `SRE-K8s`（后续扩展）
- 工作流：默认工作流已覆盖；如需要合规审计/数据治理，可新增专用工作流（后续扩展）

## 产物清单 (本任务必须落盘的文件路径)

- Team OS 规范：`AGENTS.md`、`TEAMOS.md`、`docs/*`
- 角色/工作流：`.team-os/roles/*`、`.team-os/workflows/*`
- 模板与脚本：`.team-os/templates/*`、`.team-os/scripts/*`、`scripts/teamos.sh`
- Runtime：`../team-os-runtime/docker-compose.yml`、`../team-os-runtime/README.md`、`../team-os-runtime/Makefile`、`../team-os-runtime/.env.example`
- 联网检索（如有）：`.team-os/kb/sources/*` + Skill Cards + 角色记忆索引

