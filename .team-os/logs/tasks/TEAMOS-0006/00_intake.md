# TEAMOS-0006 - 00 Intake

- 标题：DETERMINISTIC-GOV-AUDIT-v2
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 更新决定性治理审计生成器与报告，使审计覆盖新增的“项目配置 + 项目仓库 AGENTS 手册注入”治理要求，并落盘 `docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md`。

## 目标/非目标

- 目标：
- 更新 `.team-os/scripts/pipelines/audit_deterministic_gov.py`：
  - 任务证据列表纳入 `TEAMOS-0005`
  - 增加 smoke controls：project config/init+validate、project AGENTS inject（temp workspace/repo）
- 生成并落盘新的审计报告：`docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md`
- 非目标：
- 不在本任务内修改已有治理/流程脚本（除审计生成器外）。

## 约束与闸门

- 风险等级：R1
- 需要审批的动作（如有）：无

## 澄清问题 (必须回答)

- Q: 审计脚本是否允许修改真实 Workspace 真相源？
  - A: 不允许。新增的 project config/agents 注入检查使用 temp workspace/repo 进行 smoke test，避免污染真实项目数据。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/scripts/pipelines/audit_deterministic_gov.py`
- `docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md`
