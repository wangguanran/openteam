# Deterministic Governance Audit (20260217T044405Z)

## Context

- repo: /Users/wangguanran/OpenTeam/team-os
- workspace_root: /Users/wangguanran/.teamos/workspace
- git_sha: 1978e819bda5

## Task Evidence (Update Units)

- TASK-20260216-233035 TEAMOS-SCRIPT-PIPELINES
  - branch: teamos/TASK-20260216-233035-script-pipelines
  - commit: 7996c8d93e9b
  - pr: https://github.com/wangguanran/team-os/pull/2
- TEAMOS-0001 TEAMOS-AGENTS-MANUAL
  - branch: teamos/TEAMOS-0001-agents-manual
  - commit: ad1f9ab18d1e
  - pr: https://github.com/wangguanran/team-os/pull/3
- TEAMOS-0002 TEAMOS-ALWAYS-ON-SELF-IMPROVE
  - branch: teamos/TEAMOS-0002-always-on-self-improve
  - commit: 3de73c52d903
  - pr: https://github.com/wangguanran/team-os/pull/4
- TEAMOS-0003 TEAMOS-GIT-PUSH-DISCIPLINE
  - branch: teamos/TEAMOS-0003-git-push-discipline
  - commit: 82389d7ea29e
  - pr: https://github.com/wangguanran/team-os/pull/5
- TEAMOS-0004 DETERMINISTIC-GOV-AUDIT
  - branch: teamos/TEAMOS-0004-deterministic-gov-audit
  - commit: 22404ba0bda6
  - pr: https://github.com/wangguanran/team-os/pull/6
- TEAMOS-0005 TEAMOS-PROJECT-AGENTS-MANUAL
  - branch: teamos/TEAMOS-0005-project-agents-manual
  - commit: a2af586133fd
  - pr: https://github.com/wangguanran/team-os/pull/7
- TEAMOS-0006 DETERMINISTIC-GOV-AUDIT-v2
  - branch: teamos/TEAMOS-0006-deterministic-gov-audit-v2
  - commit: 96cd6deb254d
  - pr: https://github.com/wangguanran/team-os/pull/8
- TEAMOS-0007 TEAMOS-AUDIT-0001
  - branch: teamos/TEAMOS-0007-execution-strategy-audit
  - commit: 70f49feeabcf
  - pr: https://github.com/wangguanran/team-os/pull/9
- TEAMOS-0008 TEAMOS-APPROVALS-DB
  - branch: teamos/TEAMOS-0008-approvals-db
  - commit: 55feaa5da59b
  - pr: https://github.com/wangguanran/team-os/pull/10
- TEAMOS-0009 TEAMOS-CENTRAL-MODEL-ALLOWLIST
  - branch: teamos/TEAMOS-0009-central-model-allowlist
  - commit: bd221992c037
  - pr: https://github.com/wangguanran/team-os/pull/11
- TEAMOS-0010 TEAMOS-RECOVERY
  - branch: teamos/TEAMOS-0010-recovery
  - commit: 3ca5a9444649
  - pr: https://github.com/wangguanran/team-os/pull/12
- TEAMOS-0011 TEAMOS-ALWAYS-ON
  - branch: teamos/TEAMOS-0011-always-on
  - commit: d807ece44d8a
  - pr: https://github.com/wangguanran/team-os/pull/13
- TEAMOS-0012 TEAMOS-PROJECTS-SYNC
  - branch: teamos/TEAMOS-0012-projects-sync
  - commit: 1978e819bda5
  - pr: https://github.com/wangguanran/team-os/pull/14
- TEAMOS-0013 TEAMOS-VERIFY-0001
  - branch: teamos/TEAMOS-0013-verify
  - commit: 1978e819bda5
  - pr: (n/a)

## Controls (PASS/FAIL/WAIVED)

- teamos doctor: PASS  (OAuth/gh/control-plane/repo purity/workspace checks)
- Postgres DB (TEAMOS_DB_URL): PASS  (PostgreSQL reachable + migrations applied)
  - evidence: status=OK migrations=0001
- policy check: PASS  (secrets filename policy + repo/workspace governance)
- unit tests: PASS  (python3 -m unittest -q)
- requirements verify: PASS  (Raw-First drift/conflict verify (scope=teamos))
- prompt compile (dry-run): PASS  (deterministic prompt compiler (scope=teamos))
- db migrations plan (dry-run): PASS  (migration runner present (no DB writes))
- approvals list (DB-backed): PASS  (approvals readable from DB (enabled=true required))
  - evidence: db_enabled=True
- central model allowlist qualify: PASS  (TEAMOS_LLM_MODEL_ID=gpt-5 is allowed)
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

- cmd: `/Users/wangguanran/OpenTeam/team-os/teamos doctor`
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
workspace_root=/Users/wangguanran/.teamos/workspace
workspace: OK
repo: OK
```

### doctor_json

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/.team-os/scripts/pipelines/doctor.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /Users/wangguanran/.teamos/workspace --json`
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
      "pid_path": "/Users/wangguanran/OpenTeam/team-os/.team-os/state/self_improve_daemon.pid",
      "state_path": "/Users/wangguanran/OpenTeam/team-os/.team-os/state/self_improve_state.json",
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

- cmd: `/Users/wangguanran/OpenTeam/team-os/teamos policy check`
- rc: 0

```text
policy_check.repo_root=/Users/wangguanran/OpenTeam/team-os
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

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/.team-os/scripts/pipelines/requirements_raw_first.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /Users/wangguanran/.teamos/workspace verify --scope teamos`
- rc: 0

```text
{
  "ok": true,
  "project_id": "teamos",
  "scope": "teamos",
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

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/.team-os/scripts/pipelines/prompt_compile.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /Users/wangguanran/.teamos/workspace --scope teamos --dry-run`
- rc: 0

```text
{
  "ok": true,
  "scope": "teamos",
  "project_id": "teamos",
  "changed": false,
  "master_prompt_path": "/Users/wangguanran/OpenTeam/team-os/prompt-library/teamos/MASTER_PROMPT.md",
  "manifest_path": "/Users/wangguanran/OpenTeam/team-os/prompt-library/teamos/prompt_manifest.json"
}
```

### db_migrate_plan

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/.team-os/scripts/pipelines/db_migrate.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /Users/wangguanran/.teamos/workspace --dry-run`
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

- cmd: `/Users/wangguanran/OpenTeam/team-os/teamos approvals list`
- rc: 0

```text
      "status": "APPROVED",
      "requested_by": "wangguanran",
      "requested_at": "2026-02-17T04:23:44+00:00",
      "decided_by": "wangguanran",
      "decided_at": "2026-02-17T04:23:53+00:00",
      "decision_engine": "manual.verify",
      "decision_note": "verify approval record",
      "action_payload": {}
    },
    {
      "approval_id": "bd356a3b-3691-4b57-b585-8691642fdd56",
      "task_id": "TEAMOS-0013",
      "action_kind": "prod_deploy",
      "action_summary": "verify: simulate prod deploy",
      "risk_level": "HIGH",
      "risk_reasons": [
        "kind:prod_deploy"
      ],
      "category": "PROD_DEPLOY",
      "status": "DENIED",
      "requested_by": "wangguanran",
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

- cmd: `/Users/wangguanran/OpenTeam/team-os/teamos cluster qualify`
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
  "policy_path": "/Users/wangguanran/OpenTeam/team-os/.team-os/policies/central_model_allowlist.yaml"
}
```

### panel_sync_full_dry_run

- cmd: `/Users/wangguanran/OpenTeam/team-os/teamos panel sync --project teamos --full --dry-run`
- rc: 0

```text
- WOULD_CREATE_OR_UPDATE TASK TASK-20260216-120619 DONE
- WOULD_CREATE_OR_UPDATE TASK TASK-20260216-233035 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0001 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0002 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0003 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0004 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0005 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0006 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0007 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0008 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0009 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0010 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0011 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0012 DONE
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-0013 TODO
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0001 IN_PROGRESS
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0002 TODO
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0003 TODO
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0004 TODO
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0005 TODO
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0006 TODO
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0007 TODO
- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0008 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0001 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0002 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0003 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0004 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0005 TODO
- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0006 TODO
- WOULD_CREATE_OR_UPDATE MILESTONE MILESTONE:MS-CLUSTER-001 TODO
```

### project_config

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/.team-os/scripts/pipelines/project_config.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws --project demo init`
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

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/.team-os/scripts/pipelines/project_config.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws --project demo validate`
- rc: 0

```text
{
  "ok": true,
  "project_id": "demo",
  "path": "/private/var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/state/config/project.yaml"
}
```

### project_agents_inject

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/.team-os/scripts/pipelines/project_agents_inject.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws --project demo --repo-path /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/repo --manual-version v1 --no-leader-only`
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

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/.team-os/scripts/pipelines/project_agents_inject.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws --project demo --repo-path /var/folders/h1/nj29fmv90zs2trv6jkvh9_mh0000gn/T/tmpzkj3dcf3/ws/projects/demo/repo --manual-version v1 --no-leader-only`
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

- cmd: `/Users/wangguanran/OpenTeam/team-os/teamos daemon status`
- rc: 0

```text
          "- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0004 TODO",
          "- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0005 TODO",
          "- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0006 TODO",
          "- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0007 TODO",
          "- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0008 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0001 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0002 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0003 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0004 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0005 TODO",
          "- WOULD_CREATE_OR_UPDATE REQ REQ:REQ-0006 TODO",
          "- WOULD_CREATE_OR_UPDATE MILESTONE MILESTONE:MS-CLUSTER-001 TODO"
        ]
      },
      "proposal_path": "/Users/wangguanran/OpenTeam/team-os/.team-os/ledger/self_improve/20260217T042520Z-proposal.md",
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
dsn=os.environ.get('TEAMOS_DB_URL','').strip()
if not dsn:
  print(json.dumps({'ok': True, 'skipped': True, 'reason': 'TEAMOS_DB_URL not set'}))
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
{"ok": true, "skipped": false, "count": 1, "last": {"run_id": "si-20260217T042520Z", "applied_count": "3", "is_leader": "True", "trigger": "manual", "scope": "teamos", "ts": "2026-02-17 04:25:20.680881+00:00"}}
```
