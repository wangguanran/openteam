# TASK-20260216-233035 - 07 Retro

- 标题：TEAMOS-SCRIPT-PIPELINES
- 日期：2026-02-17
- 当前状态：retro

## 做得好的

- 将“任务/doctor/prompt/requirements”关键流程从零散实现迁移到可复用 pipelines 入口。
- Prompt 编译改为幂等（同输入同输出），避免时间戳/绝对路径污染真相源。

## 做得不好的/踩坑

- 运行 `teamos` CLI 时 auto self-improve 可能产生非任务化写入噪音（如 wake_events）；需要迁移为 daemon 并默认只写 gitignored state。

## 改进项 (必须写成可执行动作)

- 将 Self-Improve 从 CLI auto-wake 迁移为常驻 daemon（leader-only），并实现去重/间隔/抖动。
- 在 `task close` 后强制执行 secrets/purity/tests + commit/push（禁止跳过）。

## Team OS 自身改进建议

- doctor 增加对 pipelines/schemas/templates 的一致性检查（缺失即 FAIL）。
- 为 pipelines 增加更多单元测试（task_create/close、prompt_compile、requirements 渲染一致性）。
