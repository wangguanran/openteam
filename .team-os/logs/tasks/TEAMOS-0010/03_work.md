# TEAMOS-0010 - 03 Work

- 标题：TEAMOS-RECOVERY
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - `/v1/recovery/scan`：增加 leader-only；active task 增加 gates（NEED_PM_DECISION / WAITING_APPROVAL / BLOCKED）
  - `/v1/recovery/resume`：增加 leader-only；跳过有 gates 的任务；避免重复创建 Process-Guardian agent
  - startup：自动执行一次 recovery scan + resume（可通过 `TEAMOS_RECOVERY_AUTO=0` 关闭）
- 关键命令（含输出摘要）：
  - `python3 -m unittest -q`：PASS
  - `./teamos doctor`：PASS
- 决策与理由：
  - WAITING_APPROVAL gate 仅在 Postgres 可用时启用，避免在 sqlite 模式下引入不确定外部依赖。

## 变更文件清单

- `.team-os/templates/runtime/orchestrator/app/main.py`
