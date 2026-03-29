# Delivery Studio Runbook

## Operator Flow

Current surface today: `openteam cockpit --team delivery-studio --project <project_id>` opens the terminal-first cockpit shell. The command sequence below is the canonical delivery-studio operator contract; do not read it as a guarantee that every slash command is already wired in this branch.

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

Current reality: `panel-review/blocking-gate` is the delivery-studio review gate confirmed in this code path. The broader repo CI still uses generic job names.

Target delivery-studio protection: require these checks before merge once the delivery-studio pipeline is fully mapped to repository branch protection.

- `panel-review/blocking-gate`
- `ci/contracts`
- `ci/backend-tests`
- `ci/admin-tests`
- `ci/mobile-tests`
- `ci/integration-tests`

If the repo host cannot yet enforce the target names directly, keep the branch protection on the actual existing CI status checks and add the delivery-studio-specific checks when they are published.
