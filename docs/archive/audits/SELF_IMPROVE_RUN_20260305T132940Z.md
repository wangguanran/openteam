# Self-Improve Run Audit Snapshot

- ts: 20260305T132940Z
- repo_root: /team-os

## Checks

- missing_files: PASS
- missing_dirs: FAIL
- gitignore_missing_patterns: PASS
- roles_missing_contract_keys: PASS
- workflow_missing: FAIL
- policy_missing: FAIL
- schema_missing: FAIL
- task_artifacts_missing: PASS
- routes_missing: PASS

## Details (truncated)

```json
{
  "repo_root": "/team-os",
  "missing_files": [],
  "missing_dirs": [
    ".team-os/roles",
    ".team-os/workflows",
    ".team-os/kb/global",
    ".team-os/kb/roles",
    ".team-os/kb/platforms",
    ".team-os/kb/sources",
    ".team-os/memory/roles",
    ".team-os/ledger/team_os_issues_pending",
    ".team-os/logs/tasks",
    ".team-os/templates",
    ".team-os/scripts"
  ],
  "gitignore_missing_patterns": [],
  "repo_purity_violations": [],
  "runtime_template_mount_missing": [],
  "roles_missing_contract_keys": {},
  "workflow_missing": [
    ".team-os/workflows/trunk.yaml",
    ".team-os/workflows/plugins/"
  ],
  "policy_missing": [
    ".team-os/policies/evolution_policy.yaml"
  ],
  "schema_missing": [
    ".team-os/schemas/telemetry_event.schema.json"
  ],
  "task_artifacts_missing": {},
  "routes_missing": []
}
```
