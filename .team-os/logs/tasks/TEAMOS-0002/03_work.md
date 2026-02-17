# TEAMOS-0002 - 03 Work

- 标题：TEAMOS-ALWAYS-ON-SELF-IMPROVE
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 新增 self-improve policy：`.team-os/policies/self_improve.yaml`（enabled/interval/debounce/leader_only/dedupe/panel sync dry-run）。
  - 完整实现 self-improve daemon pipeline：`.team-os/scripts/pipelines/self_improve_daemon.py`
    - `run-once`：扫描→生成 proposals→写入 requirements（Raw-First）→写 proposal md→更新 state
    - `daemon/start/stop/status`：常驻调度与可观测性
  - `teamos` CLI：
    - 移除任意命令入口的 self-improve auto-wake（避免非任务化写入）
    - 新增 `./teamos daemon start|status|stop`
    - `./teamos self-improve` 改为调用 pipeline（决定性）
  - 修复 requirements pipeline：`.team-os/scripts/pipelines/requirements_raw_first.py`（去除无效参数 `workstreams=`，避免 self-improve 写入失败）。
  - `.gitignore`：忽略 self-improve daemon/state/pid/log（运行态文件）
- 关键命令（含输出摘要）：
  - `./teamos self-improve --force --dry-run --local` → 生成 proposal + 写入 3 条 requirements（Raw-First）
  - `./teamos daemon start` / `./teamos daemon status` → daemon pid 可观测
- 决策与理由：
  - CLI 不再 auto-wake self-improve：保证“所有真相源写入”受任务机制与闸门控制，避免日常命令产生漂移文件。
  - panel sync 默认 dry-run：Projects 作为视图层，远端写入需显式开启以满足安全闸门。

## 变更文件清单

- `.team-os/policies/self_improve.yaml`
- `.team-os/scripts/pipelines/self_improve_daemon.py`
- `.team-os/scripts/pipelines/requirements_raw_first.py`
- `teamos`
- `.gitignore`
- `docs/EXECUTION_RUNBOOK.md`
- `.team-os/ledger/self_improve/20260217T004448Z-proposal.md`
- `.team-os/ledger/self_improve/20260217T005213Z-proposal.md`
- `docs/teamos/requirements/raw_inputs.jsonl`
- `docs/teamos/requirements/requirements.yaml`
- `docs/teamos/requirements/REQUIREMENTS.md`
- `docs/teamos/requirements/CHANGELOG.md`
