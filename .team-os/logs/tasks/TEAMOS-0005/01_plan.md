# TEAMOS-0005 - 01 Plan

- 标题：TEAMOS-PROJECT-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 通过决定性 pipelines 在 Workspace 与项目仓库之间建立“可重复、可审计”的治理闭环：
  - Workspace 内维护 `project.yaml`（schema 校验、幂等读写）
  - 项目仓库根 `AGENTS.md` 自动注入 Team-OS 项目操作手册区块（标记替换；幂等；保留原内容）
  - CLI 提供统一入口并在关键事件自动触发注入（leader-only 写入）

## 拆分与里程碑

- M1（落盘与可调用）：schema/template/pipelines/CLI 命令齐备
- M2（挂钩与一致性）：config/requirements/task new 触发注入；prompt build/diff 命令落地
- M3（回归与闸门）：unittest 覆盖注入与 config；doctor/policy PASS；task ship push + PR

## 风险评估与闸门

- 风险等级：R1
- 审批点：无（不执行 R2/R3 行为）
- 闸门：
  - `./teamos policy check`
  - `python3 -m unittest -q`
  - `./teamos doctor`
  - `./teamos task close TEAMOS-0005 --scope teamos`

## 依赖

- 依赖：无新增系统级依赖（复用 stdlib + PyYAML）

## 验收标准

- `teamos project config init/show/set/validate` 可用，且写入 Workspace（不写入 team-os repo）
- `teamos project agents inject` 可对任意测试 project repo 注入/更新 AGENTS.md（幂等、保留原内容）
- `teamos prompt build/diff` 可用（与 prompt_compile 输出一致）
- 自动挂钩可用：
  - `req add/import/rebuild --scope project:<id>` 触发注入
  - `project config init/validate` 触发注入
  - `task new --scope project:<id> --mode bootstrap|upgrade` 触发注入
