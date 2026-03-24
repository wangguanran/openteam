# MASTER PROMPT (openteam)

- build_id: 2d6c380bd249d9382728198b733d97d6b9372f955cb46735f485a2b2261ad75a
- baseline_v1_sha256: db7654bd3adabca91f2751af77f10154d9ece89756f7f5eea84d5961e93a421d
- requirements_yaml_sha256: 64a27757b25d19036c17bd5d7d254bc71d6d7a75e1fe8a8dc2dc4f637d065f46
- manifest: specs/prompts/openteam/prompt_manifest.json

## Baseline (verbatim excerpt)

# Original Description (Baseline v1)

- created_at: 2026-02-17T00:00:00Z
- scope: openteam
- note: This baseline describes **OpenTeam self** requirements scope only (this repo). Project scopes must live in Workspace.

## Verbatim

OpenTeam (scope=openteam) baseline:

- Scope: only OpenTeam itself (this git repo), not external projects.
- Goal: provide a long-running, auditable, expandable OpenTeam with clear governance, safety gates, and reproducible runtime templates.
- Non-negotiables:
  - No secrets in git (only `.env.example` tracked).
  - Repo vs Workspace separation (project truth sources live outside this repo).
  - Requirements protocol v2 Raw-First: capture verbatim raw inputs before expanding.
  - Cluster leader-only writes for truth sources and remote sync.
  - OAuth-first for LLM calls via Codex CLI (`codex login`).

## Requirements Summary

### Requirements (openteam)

#### ACTIVE
- REQ-0001 [P2] CI/evals entrypoint + OpenTelemetry observability implementation plan (local-safe, remote export off by default) (ws=ai,backend,devops,general)
- REQ-0002 [P3] Workstream: governance (ws=general)
- REQ-0003 [P0] Workstream: governance (ws=ai)
- REQ-0004 [P1] Workstream: governance (ws=general)
- REQ-0005 [P1] Workstream: devops (ws=devops)
- REQ-0006 [P2] Workstream: governance (ws=general)
- REQ-0007 [P0] Enforce per-task telemetry artifacts and metrics validation (ws=backend,data,devops,general)
- REQ-0008 [P1] Formalize role contracts, taxonomy, plugin workflow, and evolution policy with doctor validation (ws=ai,backend,general)

#### NEED_PM_DECISION
- (none)

#### CONFLICT
- (none)

#### DEPRECATED
- (none)

## Operating Rules (enforced)

- No secrets in git. Use env vars only; only commit `.env.example`.
- Repo vs Workspace: project truth sources MUST be outside the openteam repo.
- Deterministic pipelines only for truth-source generation (requirements/prompt/task ledger).
- High risk actions require explicit approval (data deletion/overwrite, public ports, prod deploy, force push).
- Leader-only writes: only the elected Brain writes truth sources; assistants are read-only unless leased.
