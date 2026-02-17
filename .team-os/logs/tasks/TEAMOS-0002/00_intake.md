# TEAMOS-0002 - 00 Intake

- 标题：TEAMOS-ALWAYS-ON-SELF-IMPROVE
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 实现 leader-only 的 self-improve 常驻 daemon：周期性扫描→生成>=3改进项→落盘 proposal→写入 Team-OS requirements（Raw-First）→（可选）同步 Roadmap（dry-run）。

## 目标/非目标

- 目标：
  - 提供决定性 self-improve daemon（调度 interval/debounce/去重）。
  - 非 leader 只读扫描并上报；leader 才能写入 proposal/requirements。
  - 输出落盘：
    - `.team-os/ledger/self_improve/<ts>-proposal.md`
    - `docs/teamos/requirements/raw_inputs.jsonl`（由 pipeline 写入）并更新 Expanded（`requirements.yaml/REQUIREMENTS.md/CHANGELOG.md`）
    - `.team-os/state/self_improve_state.json`（运行态状态；gitignored）
  - CLI 支持 daemon 管理：`./teamos daemon start|status|stop`
  - 修复 requirements pipeline 以支持 self-improve 写入（决定性、schema 校验）。
- 非目标：
  - 自动 close→commit→push 的 ship 纪律（属于 `TEAMOS-GIT-PUSH-DISCIPLINE`）。
  - 真实写入 GitHub Projects（本任务默认 dry-run；远端写入需显式开启）。

## 约束与闸门

- 风险等级：R1
- 需要审批的动作（如有）：
  - 无（不开放公网端口、不强推、不删除仓库/数据；GitHub Projects 同步默认 dry-run）。

## 澄清问题 (必须回答)

- self-improve 的 “dry-run” 语义：默认仅禁止远端写入（panel sync dry-run），本地真相源写入仍会发生（符合 Raw-First 证据要求）。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：
  - 角色：Brain/Governance（leader-only writes & compliance gates）
  - 工作流：Genesis → Delivery（daemon + pipelines）→ Retro

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/policies/self_improve.yaml`
- `.team-os/scripts/pipelines/self_improve_daemon.py`
- `teamos`（移除 CLI auto-wake；新增 `daemon` 子命令）
- `.gitignore`（忽略 self-improve daemon/state runtime files）
