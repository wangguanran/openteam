# TEAMOS-0002 - 02 TODO

- 标题：TEAMOS-ALWAYS-ON-SELF-IMPROVE
- 日期：2026-02-17
- 当前状态：todo

## TODO (可并行)

- [x] 新增 self-improve policy：`.team-os/policies/self_improve.yaml`
- [x] 实现 daemon pipeline：`.team-os/scripts/pipelines/self_improve_daemon.py`（run-once/daemon/start/stop/status）
- [x] 修复 requirements pipeline：`.team-os/scripts/pipelines/requirements_raw_first.py`（兼容 requirements_store 签名）
- [x] 更新 `.gitignore`：忽略 self-improve state/pid/log（运行态）
- [x] 更新 `teamos` CLI：移除 auto-wake；新增 `daemon` 命令；`self-improve` 调用 pipeline
- [x] 运行并落证据：`./teamos self-improve --force`（>=3 proposals + requirements 更新）
- [x] 启动并验证 daemon：`./teamos daemon start`、`./teamos daemon status`
- [ ] 运行闸门：`./teamos policy check`、`./teamos doctor`、`python3 -m unittest -q`
- [ ] `./teamos task close TEAMOS-0002 --scope teamos`
- [ ] commit + push 分支：`teamos/TEAMOS-0002-always-on-self-improve`

## Skill Boot 计划 (如需联网检索)

- 主题：
- 角色：
- 预期产物：
  - 来源摘要：`.team-os/kb/sources/...`
  - Skill Card：`.team-os/kb/...`
- 记忆索引：`.team-os/memory/roles/...`

本任务不需要联网检索（无 Skill Boot）。
