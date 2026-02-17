# TEAMOS-0019 - 07 Retro

- 标题：Verify: Req v3 + locks + approvals DB
- 日期：2026-02-17
- 当前状态：retro

## 做得好的

- 端到端验收被固化为可重复脚本与报告（CLI 可调用），减少“手工验收漂移”。
- 通过审计脚本复现并定位了锁降级策略问题，并修复为更安全的行为（争用不降级）。

## 做得不好的/踩坑

- 在启用 DB 的环境下，file-lock 单测会走错 backend；需要在测试里显式设置环境隔离。

## 改进项 (必须写成可执行动作)

- 为 `teamos metrics` 增加一个可选的 `emit` 子命令（或在 `teamos` 层面统一封装），让关键命令执行可自动写入 metrics.jsonl（避免手工记录）。
- 为 self-improve 增加“重复项不落 changelog”策略（避免首次运行造成重复噪声；保持幂等）。

## Team OS 自身改进建议

- 将 `audit reqv3-locks` 的输出路径支持 `--out` 已提供；建议后续将 ts 默认策略与“覆盖已有文件”行为明确写入 runbook。
