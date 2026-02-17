# TEAMOS-0003 - 03 Work

- 标题：TEAMOS-GIT-PUSH-DISCIPLINE
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 新增决定性 ship pipeline：`.team-os/scripts/pipelines/task_ship.py`
  - CLI 新增：`./teamos task ship`
  - 强制 close→闸门→commit→push：close 失败禁止提交；push 不可用则标记 BLOCKED 并落盘原因
  - 补齐 schema：`.team-os/schemas/task_ledger.schema.json` 支持 `blocked`
  - 文档同步：`AGENTS.md` / `docs/GOVERNANCE.md` / `docs/EXECUTION_RUNBOOK.md` 增加 ship 推荐
- 关键命令（含输出摘要）：
  - （本任务将用 `./teamos task ship TEAMOS-0003 ...` 完成最终提交推送）
- 决策与理由：
  - ship 不在成功路径写回 ledger/logs（避免“一任务多提交”或 push 后产生新未提交变更）；PR URL 仅作为命令输出返回。

## 变更文件清单

- `.team-os/scripts/pipelines/task_ship.py`
- `teamos`
- `.team-os/schemas/task_ledger.schema.json`
- `AGENTS.md`
- `docs/GOVERNANCE.md`
- `docs/EXECUTION_RUNBOOK.md`
