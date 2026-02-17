# TEAMOS-0001 - 03 Work

- 标题：TEAMOS-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - `AGENTS.md`：升级为统一指导手册（任务机制/脚本优先/真相源禁止手改/Git 纪律/验收清单）。
  - `docs/GOVERNANCE.md`：补齐 Update Unit 与 Git 纪律，并明确决定性产物策略。
  - `docs/EXECUTION_RUNBOOK.md`：将 `./teamos` 作为主入口（替换历史 `scripts/teamos.sh doctor/new-task/self-improve` 引导）。
  - `.team-os/scripts/policy_check.py`：增加“文档关键短语检查”，防止回退到绕过任务机制的操作方式。
- 关键命令（含输出摘要）：
  - `TEAMOS_SELF_IMPROVE_DISABLE=1 ./teamos task new --scope teamos --title "TEAMOS-AGENTS-MANUAL" --workstreams governance` → `TEAMOS-0001`
  - `git checkout -b teamos/TEAMOS-0001-agents-manual`
- 决策与理由：
  - 将“统一任务流程”的关键命令短语纳入 `policy check`：把治理条款从文档变成可执行闸门，避免 drift。

## 变更文件清单

- `AGENTS.md`
- `docs/GOVERNANCE.md`
- `docs/EXECUTION_RUNBOOK.md`
- `.team-os/scripts/policy_check.py`
- `.team-os/logs/tasks/TEAMOS-0001/00_intake.md`
- `.team-os/logs/tasks/TEAMOS-0001/01_plan.md`
- `.team-os/logs/tasks/TEAMOS-0001/02_todo.md`
- `.team-os/logs/tasks/TEAMOS-0001/03_work.md`
