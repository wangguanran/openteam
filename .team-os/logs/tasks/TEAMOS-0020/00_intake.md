# TEAMOS-0020 - 00 Intake

- 标题：Git workflow: no per-task branches + cleanup merged temp branches
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 取消“每任务必须创建临时分支”的硬要求，允许直接在 `main` 上按 task close→ship 提交推送；并清理已合并的历史临时分支（本地 + GitHub remote）。

## 目标/非目标

- 目标：
- 更新治理文档与 `task ship` 行为：不再强制 `teamos/<TASK_ID>-...` 分支；在 `main` 上 ship 时不创建 PR。
- 修复 approvals 的 task_id 关联：支持通过 `TEAMOS_TASK_ID` env 关联 task（支持无分支工作流）。
- 清理已合并的 `origin/teamos/*` 临时分支与本地 `teamos/*` 分支（仅删除已合并到 `origin/main` 的分支）。
- 非目标：
- 不改变“每任务一提交、一推送、先 close 后 ship”的更新单位纪律。
- 不做强推/重写历史。

## 约束与闸门

- 风险等级：R2（涉及删除 remote 分支属于高风险动作，必须走 approvals）。
- 需要审批的动作（如有）：
  - 删除 GitHub remote 分支：`git push origin --delete <branch>`（HIGH）

## 澄清问题 (必须回答)

- 无。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `AGENTS.md`
- `docs/GOVERNANCE.md`
- `docs/EXECUTION_RUNBOOK.md`
- `.team-os/scripts/pipelines/task_ship.py`
- `teamos`（approvals 关联 task_id 的推断逻辑）
