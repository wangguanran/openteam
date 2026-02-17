# TEAMOS-0011 - 03 Work

- 标题：TEAMOS-ALWAYS-ON
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - Control Plane startup：best-effort 确保 self-improve daemon 运行
  - self_improve_daemon：run_once 结束后可选写入 Postgres `self_improve_runs`
  - doctor：输出 `self_improve_daemon.running` 与 pid
- 关键命令（含输出摘要）：
  - `python3 -m unittest -q`：PASS
  - `./teamos doctor`：PASS（输出 self_improve_daemon.running）
- 决策与理由：
  - DB 写入为 best-effort：未配置 `TEAMOS_DB_URL` 时跳过，避免影响 Always‑On 本体。

## 变更文件清单

- `.team-os/scripts/pipelines/self_improve_daemon.py`
- `.team-os/templates/runtime/orchestrator/app/main.py`
- `.team-os/scripts/pipelines/doctor.py`
