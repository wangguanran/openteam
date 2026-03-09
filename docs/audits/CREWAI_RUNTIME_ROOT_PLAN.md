# CREWAI Runtime Root Migration Plan (TEAMOS-AUDIT-0001)

## Scope
- Remove repository dependency on `.team-os/` paths.
- Consolidate static assets into domain directories: `specs/`, `templates/`, `scaffolds/`, `scripts/`, `migrations/`.
- Enforce runtime-only dynamic outputs under `../team-os-runtime/`.
- Keep orchestration on CrewAI + deterministic Python pipelines only.

## Hard Rules
- Repo contains static assets only.
- Runtime state/workspace/hub/logs/audit/ledger must be outside repo at `../team-os-runtime/`.
- Hub must run PostgreSQL + Redis together via Docker on Brain host.
- Leader-only writes for truth-source updates.
- Distributed lock default: PostgreSQL advisory lock; Redis for queue/event/cache.
- High-risk actions must pass approval engine and be persisted in PostgreSQL.
- Brain election must enforce `specs/policies/central_model_allowlist.yaml`.

## Runtime Root Contract
- `REPO_ROOT = git rev-parse --show-toplevel`
- `RUNTIME_ROOT = Path(REPO_ROOT).parent / "team-os-runtime"` (default)
- Optional override: `TEAMOS_RUNTIME_ROOT`

Required runtime layout:
- `../team-os-runtime/state/{audit,logs,runs,teamos,kb/sources}`
- `../team-os-runtime/workspace/projects/<id>/{repo,state/...}`
- `../team-os-runtime/hub/{compose,env,data,backups,config,logs}`
- `../team-os-runtime/{tmp,cache}`

## Module Execution Plan (Subagents)
1. Subagent B: Hub pipelines for PostgreSQL+Redis Docker lifecycle.
2. Subagent C: DB advisory locks + Redis event/queue integration.
3. Subagent D: CrewAI orchestrator tooling boundary (pipelines-only writes).
4. Subagent E: project REPL raw-input auto-capture v3 + feasibility.
5. Subagent F: remote node bootstrap + safe config push + installer knowledge.
6. Subagent G: remove deprecated external workflow layer references.
7. Subagent H: e2e tests + final runtime audit artifacts.

## Acceptance Gates
- `teamos doctor` fails if `.team-os/` exists in repo.
- `repo_purity_check` blocks runtime artifacts in repo.
- `task close` and pre-push execute purity + policy + tests.
- All dynamic reports written to `../team-os-runtime/state/audit/`.

## Risk & Approval Baseline
High-risk categories must go through approval engine (DB-backed):
- destructive operations, forced git history rewrite, system/network exposure,
- prod release/rollback/migration, secret rotation,
- repo/org critical operations, remote root install,
- hub remote exposure and remote config push with secrets.

## Deliverables
- Root-level static asset tree with updated references.
- Runtime-root deterministic path API used by all pipelines.
- Hub lifecycle CLI and pipelines (Postgres+Redis mandatory).
- Cluster + approvals + locks validated by regression tests.
