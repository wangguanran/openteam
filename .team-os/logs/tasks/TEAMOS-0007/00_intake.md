# TEAMOS-0007 - 00 Intake

- 标题：TEAMOS-AUDIT-0001
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 生成一份决定性的“执行策略全量审计”报告（PASS/FAIL/WAIVED），覆盖最新规范（DB/审批/集群/模型资格/恢复/Projects/Always‑On 等），并落盘到 `docs/audits/EXECUTION_STRATEGY_AUDIT_<ts>.md`。

## 目标/非目标

- 目标：
- 新增决定性审计生成器（pipeline + CLI 入口）：
  - `.team-os/scripts/pipelines/audit_execution_strategy.py`
  - `./teamos audit execution-strategy`
- 生成审计报告（只读检查、不修复缺口）：
  - `docs/audits/EXECUTION_STRATEGY_AUDIT_<ts>.md`
- 报告必须包含：逐条控制项 PASS/FAIL/WAIVED + 命令尾部证据（tails）+ 已落地任务证据（task/commit/PR）。
- 非目标：
- 不在本任务内修复任何 FAIL 项（修复在后续任务按 Update Unit 拆分实施）。

## 约束与闸门

- 风险等级：R1
- 需要审批的动作（如有）：无（本任务不执行高风险动作）
- 只读约束：不修改 requirements/prompt/ledger 等真相源（除审计报告本身的落盘）。

## 澄清问题 (必须回答)

- Q: 审计是否允许“为了验证”而触发写入（例如运行 self-improve/run）？
  - A: 不允许。本审计只执行只读命令与静态存在性检查；写入类验证留到后续修复与最终验收任务。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/scripts/pipelines/audit_execution_strategy.py`
- `teamos`（新增 audit 子命令）
- `docs/audits/EXECUTION_STRATEGY_AUDIT_<ts>.md`
