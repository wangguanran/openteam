# TEAMOS-0020 - 07 Retro

- 标题：Git workflow: no per-task branches + cleanup merged temp branches
- 日期：2026-02-17
- 当前状态：retro

## 做得好的

- 规则调整及时，避免 task PR 长期堆积影响主干节奏。

## 做得不好的/踩坑

- 过去依赖分支名推断 task_id 的隐式耦合在无分支工作流下失效，需要显式 env/上下文传递。

## 改进项 (必须写成可执行动作)

- 增加一个显式的 `--task-id` 全局参数（或 `teamos task use <TASK_ID>` 写入本地 state）用于 approvals/telemetry 统一关联。

## Team OS 自身改进建议

- 为“删除已合并临时分支”增加决定性 pipeline/CLI（带 dry-run + approvals gate），避免手工误删。
