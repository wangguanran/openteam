# TEAMOS-0012 - 03 Work

- 标题：TEAMOS-PROJECTS-SYNC
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - panel sync：非 dry-run 时强制 leader-only
  - Projects 字段：Repo Locator / Repo Mode
  - 同步写入：从 task ledger `repo` 元信息写入上述字段
- 关键命令（含输出摘要）：
  - `python3 -m unittest -q`：PASS
  - `./teamos doctor`：PASS
- 决策与理由：
  - 继续使用 Task ID 字段作为幂等 key（避免引入额外 DB mapping 依赖）。

## 变更文件清单

- `.team-os/templates/runtime/orchestrator/app/main.py`
- `.team-os/templates/runtime/orchestrator/app/panel_github_sync.py`
- `.team-os/integrations/github_projects/mapping.yaml`
