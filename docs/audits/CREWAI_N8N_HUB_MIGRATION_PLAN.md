# CREWAI_N8N_HUB_MIGRATION_PLAN

## Status

- Date: 2026-02-21
- Scope: teamos
- Result: PASS (implementation in progress)

## Baseline Checks

- `git status`: PASS
- `python3 -m unittest -q`: PASS
- `./teamos --help`: PASS

## Gap Summary

- Hub CLI and pipelines were missing
- Cluster/hub lock dimensions were missing
- Runtime API lacked `/v1/hub/*` and `/v1/runs/*`
- n8n hub monitor template was missing

## Milestones

1. Hub deterministic subsystem
2. Approval/risk/lock hardening
3. Runtime CrewAI + hub API integration
4. n8n presentation template
5. Docs + tests + verification

## Risks

- Docker/compose missing on host
- Redis remote exposure requires explicit approval and firewall controls
- Remote password mode requires `sshpass` on operator host
