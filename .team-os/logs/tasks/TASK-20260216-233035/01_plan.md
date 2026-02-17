# TASK-20260216-233035 - 01 Plan

- 标题：TEAMOS-SCRIPT-PIPELINES
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 新增确定性 pipelines 目录：`team-os/.team-os/scripts/pipelines/`，实现任务创建/关闭、doctor、requirements raw-first、prompt 编译等脚本入口。
- `teamos` CLI 改为优先调用 pipelines（避免依赖 Control Plane 端点存在），并保持可选 HTTP client 行为用于面板/运行态查询。
- 新增/补齐 schemas 与模板，并在 pipelines 中做 schema 校验与决定性渲染。

## 拆分与里程碑

- M1：落盘 pipelines 脚手架 + shared utils + 最小 schema validator（无第三方依赖）。
- M2：`teamos task new/close` 本地可用（scope=teamos），并在 close 中执行 metrics/policy/purity/tests 校验。
- M3：requirements raw-first pipeline 可在 scope=teamos 运行并生成 `requirements.yaml/REQUIREMENTS.md/CHANGELOG.md`。
- M4：prompt compile pipeline 生成 `prompt-library/teamos/MASTER_PROMPT.md` + manifest/history/changelog。
- M5：生成 `docs/team_os/REPO_UNDERSTANDING.md`（闸门产物）。

## 风险评估与闸门

- 风险等级：R1
- 闸门/约束：
  - 禁止 secrets 入库（push 前必须跑 secrets/purity/tests）。
  - Repo vs Workspace：任何 project scope 真相源不得写入 repo。
  - 生成真相源文件（requirements/prompt/ledger）必须由脚本产生并做 schema 校验。

## 依赖

- Python3（现用 3.9.x）+ 现有依赖：`pyyaml`、`tomli`。
- Control Plane 可用性不作为硬依赖（本任务提供本地 fallback）。

## 验收标准

- `./teamos doctor` 通过本地闸门检查（OAuth/gh/paths/repo purity/workspace）。
- `./teamos task new --scope teamos --title ...` 可创建 task artifacts。
- `./teamos task close <task_id>` 可完成 DoD 校验并将 ledger 标记 `closed`。
- `.team-os/scripts/pipelines/requirements_raw_first.py` 与 `.team-os/scripts/pipelines/prompt_compile.py` 可在 scope=teamos 运行并产生决定性输出。
