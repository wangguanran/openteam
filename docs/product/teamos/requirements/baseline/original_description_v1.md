# Original Description (Baseline v1)

- created_at: 2026-02-17T00:00:00Z
- scope: teamos
- note: This baseline describes **Team OS self** requirements scope only (this repo). Project scopes must live in Workspace.

## Verbatim

Team OS (scope=teamos) baseline:

- Scope: only Team OS itself (this git repo), not external projects.
- Goal: provide a long-running, auditable, expandable Team OS with clear governance, safety gates, and reproducible runtime templates.
- Non-negotiables:
  - No secrets in git (only `.env.example` tracked).
  - Repo vs Workspace separation (project truth sources live outside this repo).
  - Requirements protocol v2 Raw-First: capture verbatim raw inputs before expanding.
  - Cluster leader-only writes for truth sources and remote sync.
  - OAuth-first for LLM calls via Codex CLI (`codex login`).

