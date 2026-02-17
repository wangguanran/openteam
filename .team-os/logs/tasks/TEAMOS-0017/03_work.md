# TEAMOS-0017 - 03 Work

- 标题：Hotfix: restore task creation pipeline
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 修复 `.team-os/scripts/pipelines/task_create.py`：恢复可运行版本并加入并发锁（repo lock + scope lock），避免 task scaffold 并发写入破坏。
  - 锁清理使用 `atexit` 注册，降低异常退出导致锁残留风险（锁本身也带 TTL/heartbeats）。

- 关键命令（含输出摘要）：
  - `python3 -m py_compile .team-os/scripts/pipelines/task_create.py`（OK）
  - `./teamos task new --scope teamos --title "Hotfix: restore task creation pipeline" ...`（生成 `TEAMOS-0017`）
  - `python3 -m unittest -q`（OK）

- 决策与理由：
  - `task_create.py` 是统一流程入口；若其不可用，后续所有任务都无法合规创建，因此优先修复并在本任务内补齐锁与回归验证。

## 变更文件清单

- `.team-os/scripts/pipelines/task_create.py`
