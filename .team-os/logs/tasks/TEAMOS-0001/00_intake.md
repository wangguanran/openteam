# TEAMOS-0001 - 00 Intake

- 标题：TEAMOS-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 将 `AGENTS.md` 升级为 Team OS 的统一指导手册，并同步治理/执行文档，使“任务机制 + 脚本优先 + close→commit/push”成为强制流程。

## 目标/非目标

- 目标：
  - 明确并文档化：任务（Update Unit）流程、脚本优先、禁止 Agent 直写真相源、Git 纪律。
  - 同步更新 `docs/GOVERNANCE.md` 与 `docs/EXECUTION_RUNBOOK.md` 的对应章节。
  - 增加决定性校验：`policy check` 需要能检测文档是否包含关键流程（防止回退/漂移）。
- 非目标：
  - 实现 self-improve daemon（属于 `TEAMOS-ALWAYS-ON-SELF-IMPROVE`）。
  - 实现 close→commit→push 的自动化 ship 命令（属于 `TEAMOS-GIT-PUSH-DISCIPLINE`）。

## 约束与闸门

- 风险等级：R1
- 需要审批的动作（如有）：
  - 无（仅文档/本地 policy 校验；不做数据删除/公网暴露/强推等高风险操作）。

## 澄清问题 (必须回答)

- 统一入口以 `./teamos` 为准；`./scripts/teamos.sh` 作为兼容入口保留但不再作为主推荐路径。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：
  - 角色：Governance/PM-Intake（文档化与闸门校验）
  - 工作流：Genesis → Delivery（文档变更）→ Retro

## 产物清单 (本任务必须落盘的文件路径)

- `AGENTS.md`
- `docs/GOVERNANCE.md`
- `docs/EXECUTION_RUNBOOK.md`
- `.team-os/scripts/policy_check.py`（新增/强化决定性检查）
