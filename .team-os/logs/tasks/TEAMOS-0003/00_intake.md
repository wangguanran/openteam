# TEAMOS-0003 - 00 Intake

- 标题：TEAMOS-GIT-PUSH-DISCIPLINE
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 在 CLI/脚本中强制执行“task close 通过后才允许 commit/push”，并提供决定性 `task ship` 命令（失败则标记 BLOCKED 并落盘原因）。

## 目标/非目标

- 目标：
  - 新增决定性 pipeline：`.team-os/scripts/pipelines/task_ship.py`
  - CLI 新增命令：`./teamos task ship <TASK_ID>`
  - ship 强制闸门：
    - close 必须 PASS，否则禁止 commit/push
    - push 前执行 secrets scan + repo purity/policy/tests（由 close+额外扫描保证）
    - push 失败：task 标记 `blocked` 并在 `03_work.md` 与 ledger/metrics 记录原因
  - docs 同步：`AGENTS.md` / `docs/GOVERNANCE.md` / `docs/EXECUTION_RUNBOOK.md` 更新推荐命令
- 非目标：
  - 取代原生 git 命令（仍允许高级用户手工 git，但治理推荐走 ship）
  - 自动合并 PR（仅创建 PR；合并需人工决策）

## 约束与闸门

- 风险等级：R1
- 需要审批的动作（如有）：
  - 无（不强推/不重写历史；仅普通 push/PR 创建）。

## 澄清问题 (必须回答)

- ship 的 PR base 默认 `main`，但支持 `--base` 覆盖（用于堆叠分支/分阶段合并）。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：
  - 角色：Governance/Release
  - 工作流：Delivery（ship 自动化）

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/scripts/pipelines/task_ship.py`
- `teamos`（新增 `task ship` 子命令）
- `.team-os/schemas/task_ledger.schema.json`（支持 blocked）
- `AGENTS.md`
- `docs/GOVERNANCE.md`
- `docs/EXECUTION_RUNBOOK.md`
