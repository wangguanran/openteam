# TEAMOS-0001 - 07 Retro

- 标题：TEAMOS-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：retro

## 做得好的

- 文档口径与 CLI/pipelines 对齐，减少“文档说一套、工具做一套”的漂移风险。
- 将关键治理条款落到 `policy check`，把“强制”变成可执行闸门。

## 做得不好的/踩坑

- 早期文档仍遗留 `scripts/teamos.sh` 作为主入口的示例，容易诱导绕过 `task new/close`。

## 改进项 (必须写成可执行动作)

- 在 `TEAMOS-GIT-PUSH-DISCIPLINE` 中实现 `./teamos task ship <TASK_ID>`：close→gates→commit→push（并在 push 失败时标记 BLOCKED）。
- 在 `TEAMOS-ALWAYS-ON-SELF-IMPROVE` 中移除 CLI 的 auto-wake 写入，改为 daemon + leader-only。

## Team OS 自身改进建议

- 将 `AGENTS.md`/治理条款的验证从“短语匹配”逐步提升为结构化规则（例如 YAML/AST 或 markdown lint 规则），减少误报/漏报。
