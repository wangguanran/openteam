# Team-OS Layered Architecture: n8n + CrewAI + Deterministic Execution

## Overview

Team-OS is split into three layers:

1. Presentation layer: n8n workflows (`n8n/workflows/templates/*`)
2. Orchestration layer: CrewAI orchestrator (`templates/runtime/orchestrator/app/crewai_orchestrator.py`)
3. Execution layer: deterministic pipelines (`scripts/pipelines/*.py`)

n8n and CrewAI are not truth sources. Truth is persisted in:

- PostgreSQL Hub (`TEAMOS_DB_URL`)
- Team-OS repo truth files for scope=`teamos`
- Workspace truth files for scope=`project:<id>`

## Hub

Hub runs on Brain host under `~/.teamos/hub/`:

- Postgres (required)
- Redis (enabled by default, local bind by default)

Remote exposure is disabled by default and requires `teamos hub expose` + approvals.

## Write Path Rule

All state-changing operations must flow through deterministic pipelines. CrewAI tools only wrap pipelines.

## Leader Rule

In cluster mode, only leader writes global truth-source artifacts and approval decisions.

## Security

- No secrets in git
- `~/.teamos/hub/env/.env` is local-only with `0600`
- High-risk operations require approvals and audit records
