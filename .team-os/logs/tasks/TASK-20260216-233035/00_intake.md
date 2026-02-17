# TASK-20260216-233035 - 00 Intake

- 标题：TEAMOS-SCRIPT-PIPELINES
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 将 Team-OS 的关键“流程化/可重复”能力统一迁移为可确定性执行的 Python pipelines，并由 `teamos` CLI/Control Plane 调用；补齐 schema/模板/产物目录与自检闸门。

## 目标/非目标

- 目标：
  - 新增 `.team-os/scripts/pipelines/` 作为确定性入口（doctor/task/requirements/prompt/projects/self-improve 等）。
  - `teamos task new/close` 本地可用（不依赖 Control Plane 端点存在）。
  - 引入 schema 校验与决定性输出（排序、稳定 ID、manifest hash）。
  - 产出仓库理解文档闸门产物。
- 非目标：
  - 本任务不做生产发布/公网暴露。
  - 不在本任务内实现所有 GitHub Projects 真实写入（先提供幂等 dry-run + 调用链）。

## 约束与闸门

- 风险等级：R1
- 需要审批的动作（如有）：
  - 无（不执行数据删除/覆盖、不开公网端口、不强推、不做 repo 创建）。

## 澄清问题 (必须回答)

- `teamos task new` 当前依赖 Control Plane `/v1/tasks/new`（本机 Control Plane 版本可能落后而 404）。本任务将提供本地 pipeline 版本作为强制默认。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `team-os/.team-os/scripts/pipelines/*`（按要求脚本清单）
- `team-os/.team-os/schemas/prompt_manifest.schema.json`
- `team-os/.team-os/schemas/task_ledger.schema.json`
- `team-os/.team-os/templates/requirements_md.j2`
- `team-os/.team-os/templates/prompt_master.md.j2`
- `team-os/.team-os/templates/repo_understanding.md.j2`
- `team-os/docs/team_os/REPO_UNDERSTANDING.md`
