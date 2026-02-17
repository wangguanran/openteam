# TEAMOS-0020 - 02 TODO

- 标题：Git workflow: no per-task branches + cleanup merged temp branches
- 日期：2026-02-17
- 当前状态：todo

## TODO (可并行)

- [x] 更新治理文档：Git 纪律不再要求每任务一分支
- [x] 更新 `task ship`：支持无分支工作流（`main` 上 ship 不创建 PR）
- [x] 更新 approvals task_id 推断：支持 `TEAMOS_TASK_ID` env
- [x] 审批后清理分支：删除 `origin/teamos/*`（已合并）与本地 `teamos/*`
- [x] 复跑：`python3 -m unittest -q` / `./teamos policy check` / `./teamos doctor`
- [ ] `./teamos task close TEAMOS-0020` → `./teamos task ship TEAMOS-0020`

## Skill Boot 计划 (如需联网检索)

- 主题：
- 角色：
- 预期产物：
  - 来源摘要：`.team-os/kb/sources/...`
  - Skill Card：`.team-os/kb/...`
  - 记忆索引：`.team-os/memory/roles/...`
