# TEAMOS-0004 - 00 Intake

- 标题：DETERMINISTIC-GOV-AUDIT
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 生成一次“决定性流程化改造 + 治理升级 + 合规审计”总审计报告，并落盘到 `docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md`（由脚本生成）。

## 目标/非目标

- 目标：
  - 新增决定性审计报告生成器：`.team-os/scripts/pipelines/audit_deterministic_gov.py`
  - 运行并落盘审计报告：`docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md`
  - 报告包含 PASS/FAIL/WAIVED 与任务/commit/PR 证据引用
- 非目标：
  - 替代 `teamos doctor` / `policy check`（审计脚本仅汇总证据）

## 约束与闸门

- 风险等级：R1
- 需要审批的动作（如有）：
  - 无（只读检查 + 生成审计报告；不做数据删除/公网暴露/强推等）。

## 澄清问题 (必须回答)

- 报告必须由脚本生成，避免 Agent 自由文本导致的非确定性差异。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：
  - 角色：Audit/Governance
  - 工作流：Evidence → Report → Ship

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/scripts/pipelines/audit_deterministic_gov.py`
- `docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md`
