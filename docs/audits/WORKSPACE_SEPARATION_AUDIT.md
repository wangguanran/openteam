# Workspace Separation Audit

- repo: `openteam` (must stay repo-pure: only OpenTeam itself)
- audited_at: 2026-02-16
- goal: **all project truth-source files must live outside the `openteam/` git repo**, under a dedicated Workspace root (default `~/.openteam/workspace`).

## Executive Summary

Current `openteam/` repo **still contains project-scoped truth-source artifacts** (demo projects), including:

- project requirements + conflicts
- project planning overlay
- project task ledgers + logs
- project conversations

These violate the new governance rule:

> OpenTeam repo contains only OpenTeam itself. Any `project:<id>` artifacts must be outside the repo tree.

This change set will:

1. Introduce a **Workspace** root (`~/.openteam/workspace`) and enforce all project paths there.
2. Add **Repo Purity** enforcement (CLI/doctor/tests) to prevent regressions.
3. Provide a **migration tool** to move legacy project artifacts out of this repo into Workspace (default dry-run; apply requires explicit approval).

## Findings (Violations In Repo)

### 1) Project Requirements Stored In Repo (Must Move Out)

Project requirements currently exist under `openteam/docs/requirements/`:

- `docs/requirements/DEMO/` (project_id=DEMO)
- `docs/requirements/demo_panel/` (project_id=demo)

Note: OpenTeam self requirements live under `docs/product/openteam/` to avoid any `docs/requirements/` folder inside the repo.

### 2) Project Plan Overlay Stored In Repo (Must Move Out)

- `docs/plans/demo/` (project_id=demo)

### 3) Project Conversations Stored In Repo (Must Move Out)

- `.openteam/ledger/conversations/DEMO/`

### 4) Project Task Ledgers Stored In Repo (Must Move Out)

Task ledgers under `.openteam/ledger/tasks/` include non-`openteam` project scope:

- `DEMO-0001.yaml` (project_id=DEMO)
- `DEMO-PANEL-0001.yaml` (project_id=demo)
- `DEMO-PANEL-0002.yaml` (project_id=demo)
- `DEMO-PANEL-0003.yaml` (project_id=demo)
- `TASK-20260214-155102.yaml` (project_id=DEMO)
- `TASK-20260216-145106.yaml` (project_id=DEMO)

### 5) Project Task Logs Stored In Repo (Must Move Out)

Task log directories under `.openteam/logs/tasks/` corresponding to the above tasks exist in-repo and must be moved to Workspace:

- `.openteam/logs/tasks/DEMO-0001/`
- `.openteam/logs/tasks/DEMO-PANEL-0001/`
- `.openteam/logs/tasks/DEMO-PANEL-0002/`
- `.openteam/logs/tasks/DEMO-PANEL-0003/`
- `.openteam/logs/tasks/TASK-20260214-155102/`
- `.openteam/logs/tasks/TASK-20260216-145106/`

## Remediation Plan (What Will Change)

### A) Workspace Root (Outside Repo)

Default Workspace root:

- `~/.openteam/workspace` (override: CLI `--workspace-root` or config)

Structure:

```
<WORKSPACE>/
  projects/<project_id>/
    repo/
    state/
      ledger/tasks/
      logs/tasks/
      requirements/
      prompts/
      kb/
      cluster/
  shared/cache/
  shared/tmp/
  config/workspace.toml
```

### B) Control Plane + CLI Path Policy

- scope=`openteam`: allowed to write inside repo (OpenTeam self artifacts only)
- scope=`project:<id>`: must write under Workspace only
- Any attempt to write project artifacts into `openteam/` must be rejected.

### C) Migration Tool

Add a tool:

- `openteam workspace migrate --from-repo` (default dry-run)
- `openteam workspace migrate --from-repo --force` (apply; requires explicit approval)

It will relocate legacy artifacts into:

- `<WORKSPACE>/projects/<project_id>/state/...`

and remove them from the git repo tree (git will show deletions).

Note (macOS filesystem safety):

- Workspace project ids are normalized to **lowercase**.
- If the repo contains legacy ids that collide on case-insensitive filesystems (e.g. `DEMO` vs `demo`),
  the migrator will remap them deterministically (example: `DEMO -> demo-legacy`) and rewrite the migrated
  `requirements.yaml` / task ledgers accordingly.

## Acceptance Criteria

- `openteam/` contains **zero** project-scoped files (requirements/plan/ledger/logs/prompts/kb/state snapshots) for any `project_id != openteam`.
- `openteam doctor` fails if repo purity is violated.
- Evals/CI include repo purity tests.
- Project operations (req/task/chat/panel/prompt) create/read/write only under Workspace.
