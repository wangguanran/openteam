# ONE_CLICK_BOOTSTRAP_PLAN

## Goal
Provide one command startup (`./run.sh`) for Team-OS CrewAI-only runtime, with mandatory Hub (Postgres+Redis), control-plane/orchestrator readiness, and mandatory self-improve bootstrap execution even when no business tasks exist.

## Hard Constraints
- No `.team-os/` runtime path in repo.
- Repo keeps static assets only.
- Runtime artifacts must be under `../team-os-runtime` (or `TEAMOS_RUNTIME_ROOT` override).
- Hub requires both Postgres and Redis via Docker.
- High-risk actions still use approvals pipeline.
- Locks remain Postgres-advisory first, Redis queue/event/cache, file fallback.

## Runtime Contract
- `REPO_ROOT = git rev-parse --show-toplevel`
- `RUNTIME_ROOT = <REPO_ROOT>/../team-os-runtime` by default
- Required runtime dirs:
  - `state/` (audit/logs/runs/teamos)
  - `workspace/`
  - `hub/`
  - `tmp/`
  - `cache/`

## Delivery Scope
1. Add root entry script `run.sh`.
2. Add deterministic bootstrap controller `scripts/bootstrap_and_run.py`.
3. Add status/stop/restart/doctor controls (integrated in bootstrap controller; optional wrapper script if needed).
4. Enforce startup order:
   - repo purity
   - runtime dirs
   - hub init/up/health
   - hub migrate
   - control-plane start
   - crewai orchestrator readiness check
   - self-improve daemon start
   - forced self-improve bootstrap run (must reach RUNNING or COMPLETED)
   - resume unfinished tasks
5. Fix remaining `.team-os` runtime writers that break purity during startup.
6. Add tests for startup idempotency and self-improve mandatory execution.

## Acceptance Gates
- `python3 scripts/governance/check_repo_purity.py --repo-root . --json` => `ok=true`
- `python3 scripts/pipelines/repo_purity_check.py --repo-root . --workspace-root ../team-os-runtime/workspace --json` => `ok=true`
- Unit/eval tests pass.
- `./run.sh` can be executed repeatedly (idempotent).
- Startup fails if self-improve bootstrap did not actually start/finish.

## Runtime Audit Outputs
- All execution logs and diagnostics: `../team-os-runtime/state/audit/`
- Final bootstrap verification report: `../team-os-runtime/state/audit/ONE_CLICK_BOOTSTRAP_VERIFY.md`
