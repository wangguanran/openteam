# Self-Improve Run Audit Snapshot

- ts: 20260305T132940Z
- repo_root: /openteam

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
  "repo_root": "/openteam",
  "missing_files": [],
  "missing_dirs": [
    ".openteam/roles",
    ".openteam/workflows",
    ".openteam/kb/global",
    ".openteam/kb/roles",
    ".openteam/kb/platforms",
    ".openteam/kb/sources",
    ".openteam/memory/roles",
    ".openteam/ledger/openteam_issues_pending",
    ".openteam/logs/tasks",
    ".openteam/templates",
    ".openteam/scripts"
  ],
  "gitignore_missing_patterns": [],
  "repo_purity_violations": [],
  "runtime_template_mount_missing": [],
  "roles_missing_contract_keys": {},
  "workflow_missing": [
    ".openteam/workflows/trunk.yaml",
    ".openteam/workflows/plugins/"
  ],
  "policy_missing": [
    ".openteam/policies/evolution_policy.yaml"
  ],
  "schema_missing": [
    ".openteam/schemas/telemetry_event.schema.json"
  ],
  "task_artifacts_missing": {},
  "routes_missing": []
}
```
