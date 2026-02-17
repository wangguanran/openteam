# TEAMOS-0002 - 07 Retro

- 标题：TEAMOS-ALWAYS-ON-SELF-IMPROVE
- 日期：2026-02-17
- 当前状态：retro

## 做得好的

- 自我优化从“CLI auto-wake”迁移为 daemon + leader-only，避免非任务化写入污染工作区。
- self-improve 输出用决定性 pipelines 串起来（proposal→raw_inputs→expanded→panel sync dry-run）。

## 做得不好的/踩坑

- requirements_raw_first pipeline 与 runtime `add_requirement_raw_first` 参数不一致，导致 self-improve 初次写入失败（已在本任务修复）。

## 改进项 (必须写成可执行动作)

- 为 self-improve 增加 evals：覆盖 debounce/dedupe/leader-only（已自动写入 requirements：REQ-0005）。
- 在 Task4 实现 `task ship` 后，让 daemon 可选自动 ship self-improve 产物（避免 repo 长期 dirty）。

## Team OS 自身改进建议

- 进一步把 self-improve 的 proposal schema 结构化（JSON/YAML + md 渲染），便于去重与 Projects 映射。
