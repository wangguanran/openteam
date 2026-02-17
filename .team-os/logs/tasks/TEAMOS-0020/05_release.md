# TEAMOS-0020 - 05 Release

- 标题：Git workflow: no per-task branches + cleanup merged temp branches
- 日期：2026-02-17
- 当前状态：release

## 发布内容

- 更新 Git 治理规则：不再强制每任务一分支（默认允许在 main 上 ship）；修复 approvals 的 task_id 关联（env 优先）；清理已合并临时分支（需审批后执行）。

## 审批记录 (如需)

- 删除 remote 分支属于高风险动作：执行前必须通过 approvals（记录在 DB 或 fallback 审计）。
- approval_id: `98b872d3-6e84-4ab9-b372-7c0aa09af780` (status=APPROVED; decision_engine=manual.flag_yes)

## 发布步骤

```bash
./teamos task ship TEAMOS-0020 --scope teamos --summary "branchless task ship + docs + approvals task_id env"
```

## 回滚方案

- 如需回滚规则变更：`git revert <commit>`（不使用 force push）。
