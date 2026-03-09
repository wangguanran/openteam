# Workspace Separation Audit

- repo: `team-os` (must stay repo-pure: only Team OS itself)
- audited_at: 2026-02-16
- goal: **all project truth-source files must live outside the `team-os/` git repo**, under a dedicated Workspace root (default `~/.teamos/workspace`).

## Executive Summary

Current `team-os/` repo **still contains project-scoped truth-source artifacts** (demo projects), including:

- project requirements + conflicts
- project planning overlay
- project task ledgers + logs
- project conversations

These violate the new governance rule:

> Team OS repo contains only Team OS itself. Any `project:<id>` artifacts must be outside the repo tree.

This change set will:

1. Introduce a **Workspace** root (`~/.teamos/workspace`) and enforce all project paths there.
2. Add **Repo Purity** enforcement (CLI/doctor/tests) to prevent regressions.
3. Provide a **migration tool** to move legacy project artifacts out of this repo into Workspace (default dry-run; apply requires explicit approval).

## Findings (Violations In Repo)

### 1) Project Requirements Stored In Repo (Must Move Out)

Project requirements currently exist under `team-os/docs/requirements/`:

- `docs/requirements/DEMO/` (project_id=DEMO)
- `docs/requirements/demo_panel/` (project_id=demo)

Note: Team OS self requirements live under `docs/product/teamos/` to avoid any `docs/requirements/` folder inside the repo.

### 2) Project Plan Overlay Stored In Repo (Must Move Out)

- `docs/plans/demo/` (project_id=demo)

### 3) Project Conversations Stored In Repo (Must Move Out)

- `.team-os/ledger/conversations/DEMO/`

### 4) Project Task Ledgers Stored In Repo (Must Move Out)

Task ledgers under `.team-os/ledger/tasks/` include non-`teamos` project scope:

- `DEMO-0001.yaml` (project_id=DEMO)
- `DEMO-PANEL-0001.yaml` (project_id=demo)
- `DEMO-PANEL-0002.yaml` (project_id=demo)
- `DEMO-PANEL-0003.yaml` (project_id=demo)
- `TASK-20260214-155102.yaml` (project_id=DEMO)
- `TASK-20260216-145106.yaml` (project_id=DEMO)

### 5) Project Task Logs Stored In Repo (Must Move Out)

Task log directories under `.team-os/logs/tasks/` corresponding to the above tasks exist in-repo and must be moved to Workspace:

- `.team-os/logs/tasks/DEMO-0001/`
- `.team-os/logs/tasks/DEMO-PANEL-0001/`
- `.team-os/logs/tasks/DEMO-PANEL-0002/`
- `.team-os/logs/tasks/DEMO-PANEL-0003/`
- `.team-os/logs/tasks/TASK-20260214-155102/`
- `.team-os/logs/tasks/TASK-20260216-145106/`

## Remediation Plan (What Will Change)

### A) Workspace Root (Outside Repo)

Default Workspace root:

- `~/.teamos/workspace` (override: CLI `--workspace-root` or config)

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

- scope=`teamos`: allowed to write inside repo (Team OS self artifacts only)
- scope=`project:<id>`: must write under Workspace only
- Any attempt to write project artifacts into `team-os/` must be rejected.

### C) Migration Tool

Add a tool:

- `teamos workspace migrate --from-repo` (default dry-run)
- `teamos workspace migrate --from-repo --force` (apply; requires explicit approval)

It will relocate legacy artifacts into:

- `<WORKSPACE>/projects/<project_id>/state/...`

and remove them from the git repo tree (git will show deletions).

Note (macOS filesystem safety):

- Workspace project ids are normalized to **lowercase**.
- If the repo contains legacy ids that collide on case-insensitive filesystems (e.g. `DEMO` vs `demo`),
  the migrator will remap them deterministically (example: `DEMO -> demo-legacy`) and rewrite the migrated
  `requirements.yaml` / task ledgers accordingly.

## Acceptance Criteria

- `team-os/` contains **zero** project-scoped files (requirements/plan/ledger/logs/prompts/kb/state snapshots) for any `project_id != teamos`.
- `teamos doctor` fails if repo purity is violated.
- Evals/CI include repo purity tests.
- Project operations (req/task/chat/panel/prompt) create/read/write only under Workspace.
