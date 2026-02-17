# TEAMOS-0011 - 00 Intake

- 标题：TEAMOS-ALWAYS-ON
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 确保 Team‑OS 常驻后自动进入 Always‑On Self‑Improve 状态，并将每次运行记录写入 Postgres（可用时）。

## 目标/非目标

- 目标：
- Control Plane startup 自动确保 self-improve daemon 运行（best-effort）。
- self_improve_daemon.py 在 `TEAMOS_DB_URL` 配置时将 run 记录写入 `self_improve_runs`。
- doctor 增加 self-improve daemon 运行态检查（信息性输出）。
- 非目标：
- 本任务不改动 self-improve 扫描规则/产物格式（保持确定性）。
- 本任务不实现“自动 commit+push 自改代码”（仍通过任务机制人工 ship）。

## 约束与闸门

- 风险等级：R2（常驻/后台进程与 DB 审计链路）
- 需要审批的动作（如有）：无（不执行高风险动作）

## 澄清问题 (必须回答)

- Q: DB 不可用时怎么办？A: 仍可运行 daemon；DB 记录跳过并在 `self_improve_state.json` / doctor 中可见。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/scripts/pipelines/self_improve_daemon.py`（DB 记录）
- `.team-os/templates/runtime/orchestrator/app/main.py`（startup ensure daemon）
- `.team-os/scripts/pipelines/doctor.py`（daemon status）
