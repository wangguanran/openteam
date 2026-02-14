# DEMO-0001 - 02 TODO

- 标题：演示：Team OS 最小闭环（不开发具体业务）
- 日期：2026-02-14
- 当前状态：todo

## TODO (可并行)

- [ ] 运行 doctor：确认环境依赖
- [ ] 生成 Team OS repo：角色/工作流/模板/脚本/规范文档
- [ ] Skill Boot：确认以下官方事实并落盘（sources + skill card + memory index）
- [ ] 生成 runtime：compose + orchestrator skeleton + .env.example + README + Makefile
- [ ] 写执行手册：补齐备份/恢复/升级/排障/扩展
- [ ] 生成 DEMO-0001 台账与日志骨架（00~02）
- [ ] 脚本自检：`./scripts/teamos.sh doctor`、`./scripts/teamos.sh new-task`

## Skill Boot 计划 (如需联网检索)

- 主题 1：Temporal 官方 compose（Postgres）镜像名/端口/ENV
  - 来源摘要：`.team-os/kb/sources/20260214_temporal_compose_postgres.md`
  - Skill Card：`.team-os/kb/roles/Release-Ops/skill_cards/20260214_temporal_compose_postgres.md`
  - 记忆索引：`.team-os/memory/roles/Researcher/index.md`
- 主题 2：OpenHands Agent Server 镜像名/端口/风险（docker.sock）
  - 来源摘要：`.team-os/kb/sources/20260214_openhands_agent_server.md`
  - Skill Card：`.team-os/kb/roles/Release-Ops/skill_cards/20260214_openhands_agent_server.md`
  - 记忆索引：`.team-os/memory/roles/Researcher/index.md`
- 主题 3：OpenAI Agents SDK (Python) 安装与最小示例
  - 来源摘要：`.team-os/kb/sources/20260214_openai_agents_sdk_python.md`
  - Skill Card：`.team-os/kb/roles/Developer-AI/skill_cards/20260214_openai_agents_sdk_python.md`
  - 记忆索引：`.team-os/memory/roles/Researcher/index.md`

