# TEAMOS-0003 - 02 TODO

- 标题：TEAMOS-GIT-PUSH-DISCIPLINE
- 日期：2026-02-17
- 当前状态：todo

## TODO (可并行)

- [x] 更新 task ledger schema：支持 `blocked`
- [x] 实现 `task_ship` pipeline（close→secrets scan→commit→push→PR）
- [x] CLI 接入：`./teamos task ship`
- [x] 文档同步：AGENTS/GOVERNANCE/RUNBOOK 加入 ship 推荐
- [ ] 运行闸门：`./teamos policy check`、`./teamos doctor`、`python3 -m unittest -q`
- [ ] `./teamos task close TEAMOS-0003 --scope teamos`
- [ ] dogfood：`./teamos task ship TEAMOS-0003 --summary "<...>" --base <...>`

## Skill Boot 计划 (如需联网检索)

- 主题：
- 角色：
- 预期产物：
  - 来源摘要：`.team-os/kb/sources/...`
  - Skill Card：`.team-os/kb/...`
- 记忆索引：`.team-os/memory/roles/...`

本任务不需要联网检索（无 Skill Boot）。
