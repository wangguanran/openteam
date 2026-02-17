# TEAMOS-0018 - 03 Work

- 标题：Docs: Raw v3 + Self-Improve separation + Locks
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 更新 `AGENTS.md`：补齐需求协议 v3（Raw-only + Feasibility）与锁/并发要求。
  - 更新 `docs/EXECUTION_RUNBOOK.md`：将“需求处理协议 v2”升级为 v3，并补充可行性评估与 Self-Improve 分离说明。
  - 更新 `docs/GOVERNANCE.md`：将“需求处理协议 v2”升级为 v3，并补充 approvals（集群/单机差异）与锁策略说明。

- 关键命令（含输出摘要）：
  - `./teamos req add --help` / `./teamos daemon --help` / `./teamos approvals --help`（用于校验文档命令真实存在）
  - `python3 -m unittest -q`（PASS）
  - `./teamos policy check`（PASS）

- 决策与理由：
  - 文档只描述已实现命令与已落盘真相源路径，避免“看得懂但跑不通”。

## 变更文件清单

- `AGENTS.md`
- `docs/EXECUTION_RUNBOOK.md`
- `docs/GOVERNANCE.md`
