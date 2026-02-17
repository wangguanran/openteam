# TEAMOS-0003 - 07 Retro

- 标题：TEAMOS-GIT-PUSH-DISCIPLINE
- 日期：2026-02-17
- 当前状态：retro

## 做得好的

- 将“close→commit→push”固化为决定性脚本入口，减少人为遗漏与流程漂移。
- 在 ship 中加入 secrets scan 与 push 预检，失败时自动落盘 BLOCKED 原因。

## 做得不好的/踩坑

- ship 成功路径不回写 PR URL 到 ledger（避免 push 后再产生未提交变更）；需要在审计报告中引用 PR 作为外部证据。

## 改进项 (必须写成可执行动作)

- 后续可将 PR URL 记录迁移为“运行态事件/外部引用索引”（不修改真相源文件），保持“一任务一提交”原则。

## Team OS 自身改进建议

- secrets scan 可逐步增强（高熵检测/更多 token 前缀），但必须保持低误报与决定性。
