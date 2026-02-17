# TEAMOS-0020 - 03 Work

- 标题：Git workflow: no per-task branches + cleanup merged temp branches
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
- 规则变更（无分支工作流）：
  - `AGENTS.md`：Git 纪律改为“每任务一提交、一推送；分支可选（默认 main）”
  - `docs/GOVERNANCE.md` / `docs/EXECUTION_RUNBOOK.md`：移除“每任务一分支”的硬要求
  - `.team-os/scripts/pipelines/task_ship.py`：不再校验分支前缀；head==base 时跳过 PR 创建
  - `teamos`：approvals 关联 task_id 优先读取 `TEAMOS_TASK_ID` env（支持无分支工作流）
- 分支清理（待审批执行）：
  - 目标分支：`origin/teamos/*`（仅删除已合并到 `origin/main` 的分支）+ 本地同名分支
- 关键命令（含输出摘要）：
  - `git branch -r --merged origin/main | rg '^origin/teamos/'`：确认待删分支均已合并
  - `python3 .team-os/scripts/pipelines/approvals.py ... request --action-kind git_branch_delete --task-id TEAMOS-0020 --yes`：
    - approval_id=98b872d3-6e84-4ab9-b372-7c0aa09af780 status=APPROVED (single/manual.flag_yes; db=fallback)
  - `git push origin --delete <branch>`：删除 remote 分支（共 20 个，均成功）
  - `git fetch origin --prune`：确认 remote `origin/teamos/*` 为 0
  - `git branch -d <branch>`：删除本地分支（共 20 个，均成功）
- 决策与理由：
  - 取消“每任务一分支”可避免 PR 堆积；仍保留 task close→ship 的闸门与可追溯提交纪律。
  - approvals 的 task_id 不能依赖分支名，改为 env 优先以适配无分支工作流。

## 变更文件清单

- `AGENTS.md`
- `docs/GOVERNANCE.md`
- `docs/EXECUTION_RUNBOOK.md`
- `teamos`
- `.team-os/scripts/pipelines/task_ship.py`
- `.team-os/scripts/pipelines/approvals.py`
