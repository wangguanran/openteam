# TEAMOS-0012 - 00 Intake

- 标题：TEAMOS-PROJECTS-SYNC
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 强化 GitHub Projects v2 同步：leader-only 写入 + 字段覆盖补齐（Repo Locator/Repo Mode），保持幂等与可全量重建。

## 目标/非目标

- 目标：
- Panel sync 写入必须 leader-only（Brain 才能写 Projects）。
- mapping.yaml 增加字段：Repo Locator / Repo Mode，并在同步时写入。
- 保持现有幂等 upsert（Task ID key）与 `mode=full` 全量字段确保能力。
- 非目标：
- 本任务不引入删除远端 items 的 destructive rebuild（避免高风险）。
- 本任务不把 item mapping 迁移到 DB（保持现有 key_field 策略）。

## 约束与闸门

- 风险等级：R2（远端写入治理；面板字段 schema 扩展）
- 需要审批的动作（如有）：无（本任务不执行远端写入，仅实现闸门与字段扩展）

## 澄清问题 (必须回答)

- Q: Repo Locator/Mode 从哪里来？A: task ledger `repo.locator` / `repo.mode`（无则空）。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/templates/runtime/orchestrator/app/main.py`（panel sync leader-only）
- `.team-os/templates/runtime/orchestrator/app/panel_github_sync.py`（DesiredItem 增加 repo_locator/repo_mode + 写入字段）
- `.team-os/integrations/github_projects/mapping.yaml`（新增字段）
