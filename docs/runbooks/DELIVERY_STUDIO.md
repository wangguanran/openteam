# Delivery Studio Runbook

## Operator Flow

1. `openteam cockpit --team delivery-studio --project <project_id>`
2. `/new`
3. wait for `Awaiting Approval`
4. `/approve`
5. `/plan`
6. `/start`
7. `/review`
8. merge only after `panel-review/blocking-gate` and CI are green

Post-lock changes are new change requests. Do not edit the frozen request in place.

## GitHub Projects Fields

- Request ID
- Project
- Priority
- Stage
- Spec Approved
- Change Request
- Review Gate
- CI
- Release Ready
- Owner
- Blocked Reason
- Needs You

## Manual Branch Protection

Require these checks before merge:

- `panel-review/blocking-gate`
- `ci/contracts`
- `ci/backend-tests`
- `ci/admin-tests`
- `ci/mobile-tests`
- `ci/integration-tests`
