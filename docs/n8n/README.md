# n8n Presentation Layer

n8n is a visualization/automation layer only. It is not a Team-OS truth source.

## Template

- `n8n/workflows/templates/teamos_hub_monitor.json`

## Import

1. Open n8n UI
2. Import workflow JSON
3. Set env `TEAMOS_BASE_URL` for HTTP nodes

## Data Sources

Workflow reads:

- `/v1/hub/status`
- `/v1/hub/approvals`

## Security

- keep n8n private/internal
- do not store secrets in workflow JSON
- use credential store for auth if needed
