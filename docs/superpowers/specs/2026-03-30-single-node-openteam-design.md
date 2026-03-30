# Single-Node OpenTeam Design

**Status:** Draft approved in interactive design review  
**Date:** 2026-03-30  
**Primary Goal:** remove hub and multi-node infrastructure from the repository, then make OpenTeam start and run as a single-node system focused on local delivery-studio workflows.

## 1. Problem Statement

The repository currently treats `hub` as part of the default startup path:

- `./run.sh start` goes through `hub_init`, `hub_up`, `hub_health`, and `hub_migrate`
- the CLI exposes `openteam hub`, `openteam cluster`, and `openteam node`
- the control plane and bootstrap path still assume optional or default `Postgres + Redis`
- docs present hub and cluster as first-class product surfaces

This is now the wrong product shape.

The immediate product goal is:

- OpenTeam must focus on single-machine usage
- `delivery-studio` must be able to start locally without Docker, Postgres, Redis, or remote-node setup
- all hub and multi-node logic should be removed from the repository, not merely hidden

This is an intentional simplification, not a compatibility release.

## 2. Product Decision

OpenTeam will become a **single-node system** with:

- CLI as the user-facing entry surface
- a local control plane process for runtime coordination
- local runtime state under `~/.openteam/runtime/default`
- local workspace state under `~/.openteam/workspace` or the configured workspace root
- `SQLite` runtime persistence via `runtime.db`

OpenTeam will no longer ship:

- local hub management
- Docker-managed local Postgres/Redis bootstrap
- cluster leadership and assistant-node coordination
- remote node bootstrap / join flows
- hub-based secret distribution to remote nodes

## 3. Target Runtime Shape

The target runtime shape is:

1. `./run.sh start`
2. ensure runtime/workspace layout
3. validate LLM auth
4. ensure local runtime DB is ready
5. start local control plane on loopback
6. bootstrap default team
7. resume unfinished local tasks

The target startup path must not require:

- Docker
- Docker Compose
- `Postgres`
- `Redis`
- hub env/config/compose generation
- cluster election

## 4. Architecture Boundaries

### 4.1 Keep

These surfaces stay:

- `run.sh`
- `scripts/bootstrap_and_run.py`
- local control plane
- `delivery-studio`
- workspace-backed requirement and task artifacts
- local `runtime.db`
- GitHub Projects sync as an optional remote view layer
- GitHub checks as an optional remote write layer when configured

### 4.2 Remove

These surfaces are deleted:

- `openteam hub ...`
- `openteam cluster ...`
- `openteam node ...`
- `scripts/pipelines/hub_*`
- `scripts/cluster/*`
- hub compose/env/data/config generators
- hub-specific docs and templates
- remote hub config export/push flows
- cluster election and node registry paths
- Redis-only coordination paths

### 4.3 Simplify

These surfaces stay but must be simplified:

- `doctor`
  It must stop treating hub and cluster APIs as part of required runtime readiness.

- `status`
  It must report only single-node health:
  - repo root
  - runtime root
  - workspace root
  - llm config
  - control plane
  - default team state
  - local runtime DB backend

- `bootstrap_and_run.py`
  It must no longer construct DB/Redis URLs from hub env or wait on hub health.

## 5. Single-Node Persistence Policy

The single-node persistence model is:

- runtime coordination state lives in local `runtime.db`
- workspace truth sources remain in workspace directories
- runtime logs, audit, cache, and temporary files remain under runtime root

The repository should stop advertising `Postgres` as the default or expected backend for local operation.

Preferred product direction after this cut:

- `SQLite` becomes the only supported local persistence backend
- any later distributed or multi-host design will be reintroduced by a new design, not by leaving legacy hub code behind

## 6. CLI and Control Plane Contract

### 6.1 CLI

After this cut:

- `openteam hub` is removed
- `openteam cluster` is removed
- `openteam node` is removed

The supported local path remains:

- `openteam cockpit`
- `openteam team ...`
- `openteam workspace ...`
- `openteam panel ...`
- `openteam req ...`
- `./run.sh start|status|stop|restart|doctor`

### 6.2 Control Plane

The control plane remains part of the architecture because:

- `cockpit`
- `team watch`
- SSE/event surfaces
- long-running delivery workflows
- status aggregation

all benefit from a local daemon instead of one-shot CLI-only scripts.

The control plane remains local-only in this design:

- loopback binding
- no hub dependency
- no cluster election

## 7. Delivery-Studio Expectations After the Cut

This cut is not a full delivery-studio Phase 2 implementation. It only guarantees that the existing implemented subset remains usable in a single-node environment.

The required surviving delivery-studio lifecycle is:

- `Discussing`
- `Awaiting Approval`
- `Locked`
- `Changes Requested`
- `CI Running`

The cut must preserve:

- request creation in workspace-backed state
- approval draft / approval record artifacts
- change request creation
- review veto and test-completeness gate
- GitHub Projects main-card projection
- cockpit shell entrypoint

This cut does **not** require implementing the remaining planned lifecycle states:

- `Docs Updating`
- `Plan Ready`
- `Implementing`
- `Ready to Merge`
- `Merged`

Those remain a later delivery-studio product phase.

## 8. Deletion Scope

The implementation plan must remove or refactor all repo surfaces that still make hub or multi-node behavior part of the normal product contract.

### 8.1 Code and CLI

- remove hub CLI handlers and parser entries
- remove cluster/node CLI handlers and parser entries
- remove hub pipelines and cluster scripts
- remove hub status logic from bootstrap/status/doctor
- remove cluster and hub API endpoints from control plane where still present

### 8.2 Docs

- remove hub and cluster from README primary product story
- remove hub-first setup instructions from runbooks
- remove hub/cluster security and governance instructions that no longer apply
- update repo understanding docs to the new single-node reality

### 8.3 Runtime assumptions

- remove hub env generation
- remove compose file generation and compose shelling
- remove Redis health checks from bootstrap
- stop exporting runtime DB/Redis URLs from hub env into the local control plane

## 9. Compatibility Policy

This cut is intentionally breaking.

The repository will not keep no-op or deprecated `hub/cluster/node` shells just for command compatibility.

Reason:

- keeping dead shells preserves false product surface
- it increases maintenance cost
- it conflicts with the explicit user decision to clear hub-related logic before redesign

The repository should fail clearly if stale documentation or automation still refers to removed commands.

## 10. Risks and Constraints

### 10.1 Main risk

The main risk is incomplete deletion:

- startup code still reaching for hub state
- docs still instructing users to initialize hub
- tests still expecting cluster or hub endpoints
- control-plane code still importing hub/cluster-only modules

### 10.2 Secondary risk

Some existing optional database paths may still be wired for `Postgres`.

This cut must decide whether to:

- remove those branches now, or
- keep them as dormant optional code while ensuring they are no longer part of local startup or docs

Recommended implementation bias:

- remove startup/runtime dependence first
- remove optional dormant branches where low risk
- do not expand scope into unrelated runtime rewrites

## 11. Acceptance Criteria

This design is complete only when all of the following are true:

1. `./run.sh start` can start local OpenTeam without Docker, Postgres, or Redis.
2. `./run.sh doctor` does not require hub or cluster readiness.
3. `./run.sh status` no longer reports hub state.
4. `openteam hub`, `openteam cluster`, and `openteam node` are gone.
5. Hub and cluster scripts are removed from the repo.
6. Delivery-studio tests still pass.
7. New single-node bootstrap tests prove the simplified startup path.
8. Docs consistently describe OpenTeam as a single-node local system.

## 12. Out of Scope

This design does not include:

- a new distributed hub architecture
- a replacement multi-node coordination design
- full delivery-studio lifecycle completion
- production deployment orchestration redesign
- GitHub branch protection automation

Those belong to later specs.

## 13. Recommended Implementation Order

1. Remove hub from bootstrap, doctor, and status.
2. Ensure local control-plane startup works with only local runtime DB.
3. Remove CLI commands and scripts for hub/cluster/node.
4. Remove control-plane hub/cluster endpoints and dead imports.
5. Update docs and runbooks.
6. Add and run single-node startup tests plus delivery-studio regression tests.

This order prioritizes restoring a runnable local product before broad cleanup.
