# Deterministic Governance Audit (20260217T044405Z)

## Context

- repo: /Users/openteam-dev/OpenTeam/openteam
- workspace_root: /Users/openteam-dev/.openteam/workspace
- git_sha: 1978e819bda5

## Task Evidence (Update Units)

- TASK-20260216-233035 OPENTEAM-SCRIPT-PIPELINES
  - branch: openteam/TASK-20260216-233035-script-pipelines
  - commit: 7996c8d93e9b
  - pr: https://github.com/openteam-dev/openteam/pull/2
- OPENTEAM-0001 OPENTEAM-AGENTS-MANUAL
  - branch: openteam/OPENTEAM-0001-agents-manual
  - commit: ad1f9ab18d1e
  - pr: https://github.com/openteam-dev/openteam/pull/3
- OPENTEAM-0002 OPENTEAM-ALWAYS-ON-SELF-IMPROVE
  - branch: openteam/OPENTEAM-0002-always-on-self-improve
  - commit: 3de73c52d903
  - pr: https://github.com/openteam-dev/openteam/pull/4
- OPENTEAM-0003 OPENTEAM-GIT-PUSH-DISCIPLINE
  - branch: openteam/OPENTEAM-0003-git-push-discipline
  - commit: 82389d7ea29e
  - pr: https://github.com/openteam-dev/openteam/pull/5
- OPENTEAM-0004 DETERMINISTIC-GOV-AUDIT
  - branch: openteam/OPENTEAM-0004-deterministic-gov-audit
  - commit: 22404ba0bda6
  - pr: https://github.com/openteam-dev/openteam/pull/6
- OPENTEAM-0005 OPENTEAM-PROJECT-AGENTS-MANUAL
  - branch: openteam/OPENTEAM-0005-project-agents-manual
  - commit: a2af586133fd
  - pr: https://github.com/openteam-dev/openteam/pull/7
- OPENTEAM-0006 DETERMINISTIC-GOV-AUDIT-v2
  - branch: openteam/OPENTEAM-0006-deterministic-gov-audit-v2
  - commit: 96cd6deb254d
  - pr: https://github.com/openteam-dev/openteam/pull/8
- OPENTEAM-0007 OPENTEAM-AUDIT-0001
  - branch: openteam/OPENTEAM-0007-execution-strategy-audit
  - commit: 70f49feeabcf
  - pr: https://github.com/openteam-dev/openteam/pull/9
- OPENTEAM-0008 OPENTEAM-APPROVALS-DB
  - branch: openteam/OPENTEAM-0008-approvals-db
  - commit: 55feaa5da59b
  - pr: https://github.com/openteam-dev/openteam/pull/10
- OPENTEAM-0009 OPENTEAM-CENTRAL-MODEL-ALLOWLIST
  - branch: openteam/OPENTEAM-0009-central-model-allowlist
  - commit: bd221992c037
  - pr: https://github.com/openteam-dev/openteam/pull/11
- OPENTEAM-0010 OPENTEAM-RECOVERY
  - branch: openteam/OPENTEAM-0010-recovery
  - commit: 3ca5a9444649
  - pr: https://github.com/openteam-dev/openteam/pull/12
- OPENTEAM-0011 OPENTEAM-ALWAYS-ON
  - branch: openteam/OPENTEAM-0011-always-on
  - commit: d807ece44d8a
  - pr: https://github.com/openteam-dev/openteam/pull/13
- OPENTEAM-0012 OPENTEAM-PROJECTS-SYNC
  - branch: openteam/OPENTEAM-0012-projects-sync
  - commit: 1978e819bda5
  - pr: https://github.com/openteam-dev/openteam/pull/14
- OPENTEAM-0013 OPENTEAM-VERIFY-0001
  - branch: openteam/OPENTEAM-0013-verify
  - commit: 1978e819bda5
  - pr: (n/a)

## Controls (PASS/FAIL/WAIVED)

- openteam doctor: PASS  (OAuth/gh/control-plane/repo purity/workspace checks)
- Postgres DB (OPENTEAM_DB_URL): PASS  (PostgreSQL reachable + migrations applied)
  - evidence: status=OK migrations=0001
- policy check: PASS  (secrets filename policy + repo/workspace governance)
- unit tests: PASS  (python3 -m unittest -q)
- requirements verify: PASS  (Raw-First drift/conflict verify (scope=openteam))
- prompt compile (dry-run): PASS  (deterministic prompt compiler (scope=openteam))
- db migrations plan (dry-run): PASS  (migration runner present (no DB writes))
- approvals list (DB-backed): PASS  (approvals readable from DB (enabled=true required))
  - evidence: db_enabled=True
- central model allowlist qualify: PASS  (OPENTEAM_LLM_MODEL_ID=gpt-5 is allowed)
- panel sync (dry-run full): PASS  (GitHub Projects sync is idempotent; dry-run produces action plan only)
- project config (smoke): PASS  (workspace-local project.yaml init (temp workspace))
- project config validate (smoke): PASS  (schema validate (temp workspace))
- project AGENTS injection (smoke): PASS  (idempotent AGENTS.md injection (temp workspace/repo))
- project AGENTS injection idempotent: PASS  (second run should be no-op)
- self-improve daemon status: PASS  (daemon status/state readable; must be running)
- self-improve runs recorded (DB): PASS  (self_improve_runs count>=1 when DB enabled)
  - evidence: count=1 skipped=False

## Evidence (command tails)

### doctor

- cmd: `/Users/openteam-dev/OpenTeam/openteam/openteam doctor`
- rc: 0

```text
repo_purity.ok=true violations=0
profile=panel base_url=http://127.0.0.1:8787
control_plane: OK instance_id=61e7d5c6-7d5c-43fd-96f0-edf6f11a97cc
control_plane_api: OK
codex: OK Logged in using ChatGPT
gh: OK OK logged_in=true
db: OK 
self_improve_daemon.running=true pid=67830
workspace_root=/Users/openteam-dev/.openteam/workspace
workspace: OK
repo: OK
```

### doctor_json

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/openteam-dev/OpenTeam/openteam/scripts/pipelines/doctor.py --repo-root /Users/openteam-dev/OpenTeam/openteam --workspace-root /Users/openteam-dev/.openteam/workspace --json`
- rc: 0

```text
      "message": "OK logged_in=true"
    },
    "postgres_db": {
      "ok": true,
      "status": "OK",
      "dsn": "set",
      "migrations": [
        "0001"
      ]
    },
    "self_improve_daemon": {
      "ok": true,
      "running": true,
      "pid": 67830,
      "pid_path": "/Users/openteam-dev/OpenTeam/openteam/.openteam/state/self_improve_daemon.pid",
      "state_path": "/Users/openteam-dev/OpenTeam/openteam/.openteam/state/self_improve_state.json",
      "state_exists": true
    },
    "control_plane": {
      "base_url": "http://127.0.0.1:8787",
      "ok": true,
      "healthz": "ok",
      "instance_id": "61e7d5c6-7d5c-43fd-96f0-edf6f11a97cc",
      "api_coverage": {
        "ok": true,
        "missing_paths": []
      }
    }
  }
}
```

### policy

- cmd: `/Users/openteam-dev/OpenTeam/openteam/openteam policy check`
- rc: 0

```text
policy_check.repo_root=/Users/openteam-dev/OpenTeam/openteam
policy_check.ok=True failures=0 warnings=0
```

### unittest

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 -m unittest -q`
- rc: 0

```text
----------------------------------------------------------------------
Ran 25 tests in 0.602s

OK
```

### req_verify

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/openteam-dev/OpenTeam/openteam/scripts/pipelines/requirements_raw_first.py --repo-root /Users/openteam-dev/OpenTeam/openteam --workspace-root /Users/openteam-dev/.openteam/workspace verify --scope openteam`
- rc: 0

```text
{
  "ok": true,
  "project_id": "openteam",
  "scope": "openteam",
  "drift": {
    "ok": true,
    "need_pm_decision": false,
    "points": []
  },
  "conflicts": [],
  "_generated_at": "2026-02-17T04:44:08Z"
}
```

### prompt_compile

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/openteam-dev/OpenTeam/openteam/scripts/pipelines/prompt_compile.py --repo-root /Users/openteam-dev/OpenTeam/openteam --workspace-root /Users/openteam-dev/.openteam/workspace --scope openteam --dry-run`
- rc: 0

```text
{
  "ok": true,
  "scope": "openteam",
  "project_id": "openteam",
  "changed": false,
  "master_prompt_path": "/Users/openteam-dev/OpenTeam/openteam/prompt-library/openteam/MASTER_PROMPT.md",
  "manifest_path": "/Users/openteam-dev/OpenTeam/openteam/prompt-library/openteam/prompt_manifest.json"
}
```

### db_migrate_plan

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/openteam-dev/OpenTeam/openteam/scripts/pipelines/db_migrate.py --repo-root /Users/openteam-dev/OpenTeam/openteam --workspace-root /Users/openteam-dev/.openteam/workspace --dry-run`
- rc: 0

```text
{
  "ok": true,
  "dry_run": true,
  "migrations": [
    {
      "version": "0001",
      "file": "0001_init.sql"
    }
  ]
}
```

### approvals_list

- cmd: `/Users/openteam-dev/OpenTeam/openteam/openteam approvals list`
- rc: 0

```text
      "status": "APPROVED",
      "requested_by": "openteam-dev",
      "requested_at": "2026-02-17T04:23:44+00:00",
      "decided_by": "openteam-dev",
      "decided_at": "2026-02-17T04:23:53+00:00",
      "decision_engine": "manual.verify",
      "decision_note": "verify approval record",
      "action_payload": {}
    },
    {
      "approval_id": "bd356a3b-3691-4b57-b585-8691642fdd56",
      "task_id": "OPENTEAM-0013",
      "action_kind": "prod_deploy",
      "action_summary": "verify: simulate prod deploy",
      "risk_level": "HIGH",
      "risk_reasons": [
        "kind:prod_deploy"
      ],
      "category": "PROD_DEPLOY",
      "status": "DENIED",
      "requested_by": "openteam-dev",
      "requested_at": "2026-02-17T04:23:34+00:00",
      "decided_by": "policy",
      "decided_at": "2026-02-17T04:23:34+00:00",
      "decision_engine": "policy.always_deny",
      "decision_note": "category denied: PROD_DEPLOY",
      "action_payload": {}
    }
  ]
}
```

### cluster_qualify_allowed

- cmd: `/Users/openteam-dev/OpenTeam/openteam/openteam cluster qualify`
- rc: 0

```text
{
  "ok": true,
  "llm_profile": {
    "provider": "codex",
    "model_id": "gpt-5",
    "auth_mode": "oauth"
  },
  "qualification": {
    "qualified": true,
    "reason": "allowed",
    "model_id": "gpt-5",
    "allowed_model_ids": [
      "gpt-4.1",
      "gpt-5",
      "o3"
    ]
  },
  "policy_path": "/Users/openteam-dev/OpenTeam/openteam/policies/central_model_allowlist.yaml"
}
```

### panel_sync_full_dry_run

- cmd: `/Users/openteam-dev/OpenTeam/openteam/openteam panel sync --project openteam --full --dry-run`
- rc: 0

```text
- WOULD_CREATE_OR_UPDATE TASK TASK-20260216-120619 DONE
- WOULD_CREATE_OR_UPDATE TASK TASK-20260216-233035 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0001 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0002 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0003 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0004 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0005 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0006 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0007 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0008 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0009 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0010 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0011 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0012 DONE
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-0013 TODO
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0001 IN_PROGRESS
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0002 TODO
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0003 TODO
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0004 TODO
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0005 TODO
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0006 TODO
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0007 TODO
- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0008 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0001 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0002 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0003 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0004 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0005 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0006 TODO
- WOULD_CREATE_OR_UPDATE MILESTONE MILESTONE:MS-CLUSTER-001 TODO
```

### project_config

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/openteam-dev/OpenTeam/openteam/scripts/pipelines/project_config.py --repo-root /Users/openteam-dev/OpenTeam/openteam --workspace-root /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws --project demo init`
- rc: 0

```text
{
  "ok": true,
  "project_id": "demo",
  "path": "/private/var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/state/config/project.yaml",
  "changed": true,
  "dry_run": false
}
```

### project_config_validate

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/openteam-dev/OpenTeam/openteam/scripts/pipelines/project_config.py --repo-root /Users/openteam-dev/OpenTeam/openteam --workspace-root /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws --project demo validate`
- rc: 0

```text
{
  "ok": true,
  "project_id": "demo",
  "path": "/private/var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/state/config/project.yaml"
}
```

### project_agents_inject

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/openteam-dev/OpenTeam/openteam/scripts/pipelines/project_agents_inject.py --repo-root /Users/openteam-dev/OpenTeam/openteam --workspace-root /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws --project demo --repo-path /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/repo --manual-version v1 --no-leader-only`
- rc: 0

```text
{
  "ok": true,
  "project_id": "demo",
  "project_repo_path": "/private/var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/repo",
  "agents_path": "/private/var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/repo/AGENTS.md",
  "manual_version": "v1",
  "mode": "create",
  "changed": true,
  "wrote": true,
  "dry_run": false,
  "leader_only": false,
  "leader": {
    "ok": false,
    "is_leader": false,
    "reason": "unknown"
  }
}
```

### project_agents_inject_idempotent

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/openteam-dev/OpenTeam/openteam/scripts/pipelines/project_agents_inject.py --repo-root /Users/openteam-dev/OpenTeam/openteam --workspace-root /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws --project demo --repo-path /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/repo --manual-version v1 --no-leader-only`
- rc: 0

```text
{
  "ok": true,
  "project_id": "demo",
  "project_repo_path": "/private/var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/repo",
  "agents_path": "/private/var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/repo/AGENTS.md",
  "manual_version": "v1",
  "mode": "replace",
  "changed": false,
  "wrote": false,
  "dry_run": false,
  "leader_only": false,
  "leader": {
    "ok": false,
    "is_leader": false,
    "reason": "unknown"
  }
}
```

### daemon_status

- cmd: `/Users/openteam-dev/OpenTeam/openteam/openteam daemon status`
- rc: 0

```text
          "- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0004 TODO",
          "- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0005 TODO",
          "- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0006 TODO",
          "- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0007 TODO",
          "- WOULD_CREATE_OR_UPDATE TASK OPENTEAM-CLUSTER-0008 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0001 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0002 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0003 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0004 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0005 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0006 TODO",
          "- WOULD_CREATE_OR_UPDATE MILESTONE MILESTONE:MS-CLUSTER-001 TODO"
        ]
      },
      "proposal_path": "/Users/openteam-dev/OpenTeam/openteam/.openteam/ledger/self_improve/20260217T042520Z-proposal.md",
      "ts": "2026-02-17T04:25:20Z",
      "wrote_truth": true
    },
    "leader": {
      "base_url": "http://127.0.0.1:8787",
      "instance_id": "61e7d5c6-7d5c-43fd-96f0-edf6f11a97cc",
      "is_leader": true,
      "leader_instance_id": "61e7d5c6-7d5c-43fd-96f0-edf6f11a97cc",
      "ok": true
    },
    "next_run_at": "2026-02-17T05:25:20Z",
    "policy_sha256": "3c3e91f2a692da01676617ee0e414e71dfeac7cbe35dd4bfe15d938ab410dd30",
    "schema_version": 1
  }
}
```

### db_self_improve_runs

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 -c import json, os
try:
  import psycopg
  from psycopg.rows import dict_row
except Exception as e:
  raise SystemExit('missing psycopg')
dsn=os.environ.get('OPENTEAM_DB_URL','').strip()
if not dsn:
  print(json.dumps({'ok': True, 'skipped': True, 'reason': 'OPENTEAM_DB_URL not set'}))
  raise SystemExit(0)
with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
  with conn.cursor() as cur:
    cur.execute('SELECT count(*) AS n FROM self_improve_runs')
    n = int(cur.fetchone()['n'])
    cur.execute('SELECT run_id, applied_count, is_leader, trigger, scope, ts FROM self_improve_runs ORDER BY ts DESC LIMIT 1')
    last = cur.fetchone()
out={'ok': True, 'skipped': False, 'count': n, 'last': {k: str(v) for k, v in (dict(last) if last else {}).items()}}
print(json.dumps(out, ensure_ascii=False))
`
- rc: 0

```text
{"ok": true, "skipped": false, "count": 1, "last": {"run_id": "si-20260217T042520Z", "applied_count": "3", "is_leader": "True", "trigger": "manual", "scope": "openteam", "ts": "2026-02-17 04:25:20.680881+00:00"}}
```
