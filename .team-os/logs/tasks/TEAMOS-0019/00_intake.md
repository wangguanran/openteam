# TEAMOS-0019 - 00 Intake

- 标题：Verify: Req v3 + locks + approvals DB
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 为 REQv3（Raw-only + Feasibility）+ Self-Improve 分离 + 并发锁 + 审批(DB) 做端到端验收，并将验收固化为可重复的 `teamos audit reqv3-locks` 审计脚本与落盘报告。

## 目标/非目标

- 目标：
- 生成并落盘确定性审计报告：`docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md`（PASS/FAIL 证据齐全）。
- 将审计流程落盘为脚本并接入 CLI：`.team-os/scripts/pipelines/audit_reqv3_locks.py` + `./teamos audit reqv3-locks`。
- 验证并修复：DB 锁争用不应降级为 file lock（仅 DB 不可用才降级）。
- 验证：Self-Improve 不写 `raw_inputs.jsonl`，通过 system channel 更新 Expanded（可产生提案文件与 changelog 证据）。
- 验证：审批记录可写入 Postgres（不执行真实 GitHub 仓库创建，仅验证审批引擎落盘）。
- 非目标：
- 不做生产发布/线上迁移；不新增对外暴露端口的服务。
- 不在本任务中重构 Self-Improve 的提案策略质量（仅验收通道与隔离/去重/落盘）。

## 约束与闸门

- 风险等级：R1（验证类变更为主）。
- 需要审批的动作（如有）：仅模拟 `action_kind=repo_create` 的 HIGH 风险审批记录写入 DB（无真实仓库创建）。

## 澄清问题 (必须回答)

- 无。

## 需要哪些角色/工作流扩展

- 角色：Platform/QA
- 工作流：Genesis → Verify → Close

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/scripts/pipelines/audit_reqv3_locks.py`
- `docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md`
- `.team-os/ledger/self_improve/20260217T091621Z-proposal.md`（验证 self-improve 落盘与 system channel）
- `.team-os/scripts/pipelines/locks.py`（DB 锁争用行为修复）
