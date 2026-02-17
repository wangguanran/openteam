# TEAMOS-0002 - 01 Plan

- 标题：TEAMOS-ALWAYS-ON-SELF-IMPROVE
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 用决定性 pipeline 实现 self-improve 的 scan→propose→apply（requirements Raw-First）与 daemon 调度，并在 CLI 中提供 daemon 管理命令；同时移除 CLI 的 auto-wake 以避免非任务化写入。

## 拆分与里程碑

- M1：补齐配置与运行态 state
  - 新增 `.team-os/policies/self_improve.yaml`
  - 新增/忽略 `.team-os/state/self_improve_state.json`（gitignored）
- M2：实现 `.team-os/scripts/pipelines/self_improve_daemon.py`
  - run-once + daemon loop + start/stop/status
  - leader-only 写入
  - interval/debounce/去重
- M3：CLI 集成
  - 移除 `teamos` main 的 auto-wake
  - `./teamos self-improve` 调用 pipeline
  - 新增 `./teamos daemon start|status|stop`
- M4：落证据（无项目也能产出 >=3 改进项并写入 requirements）
  - `./teamos self-improve --force`
  - 检查 proposal/state/requirements 产物

## 风险评估与闸门

- 风险等级：R?
- 审批点：
  - ...
  - 风险等级：R1
  - 审批点：无（panel sync 默认 dry-run；不做 GitHub 写入）

## 依赖

- Control Plane 本机可达（用于 leader 判断与 panel sync dry-run），默认 `http://127.0.0.1:8787`。

## 验收标准

- `./teamos daemon start|status|stop` 可用
- `./teamos self-improve --force` 可在无项目情况下生成 >=3 改进项并落盘：
  - `.team-os/ledger/self_improve/<ts>-proposal.md`
  - `docs/teamos/requirements/{raw_inputs.jsonl,requirements.yaml,REQUIREMENTS.md,CHANGELOG.md}` 更新
  - `.team-os/state/self_improve_state.json` 更新 last_run/next_run/last_errors/dedupe
- `./teamos doctor`：PASS
