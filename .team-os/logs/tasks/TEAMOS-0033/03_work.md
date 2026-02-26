# TEAMOS-0033 - 03 Work

- 标题：TEAMOS-CREWAI-UNIFY-PROCESS
- 日期：2026-02-26
- 当前状态：work

## 实施记录

- 变更点：
  - `crewai_orchestrator.py`：改为 CrewAI Flow 驱动，支持 `genesis/standard/maintenance/migration`，兼容 `pipeline:<name>`。
  - `main.py`：`RunStartIn` 新增 `flow`（兼容 `pipeline`）；health check 改为检测 `crewai_orchestrator_exists`；任务 scaffold 增加 `orchestration` 字段。
  - `task_create.py`：新建任务台账增加 `orchestration: {engine: crewai, flow: genesis}`。
  - 文档：`README.md`、`TEAMOS.md`、`docs/EXECUTION_RUNBOOK.md`、`.team-os/templates/runtime/README.md` 更新为 CrewAI 统一口径。

- 关键命令（含输出摘要）：
  - `./teamos task new --scope teamos --title "TEAMOS-CREWAI-UNIFY-PROCESS" --workstreams "architecture,governance"`
    - 输出：创建任务 `TEAMOS-0033`。
  - `python3 -m unittest -q`
    - 输出：`Ran 39 tests ... OK`。

- 决策与理由：
  - 保留 `.team-os/workflows/` 目录作为 Crew Flow 定义承载，避免破坏现有治理与兼容逻辑。
  - 保留旧 `pipeline` 入参兼容，避免 API 调用方回归。

## 变更文件清单

- `.team-os/templates/runtime/orchestrator/app/crewai_orchestrator.py`
- `.team-os/templates/runtime/orchestrator/app/main.py`
- `.team-os/scripts/pipelines/task_create.py`
- `.team-os/templates/runtime/README.md`
- `README.md`
- `TEAMOS.md`
- `docs/EXECUTION_RUNBOOK.md`

- 完成后动作：
  - 执行 `./teamos task close TEAMOS-0033 --scope teamos` 通过。
  - 从 `/home/wangguanran/TODO.md` 移除已完成条目并记录到本任务日志。
