# Deterministic Governance Audit (20260217T011242Z)

## Context

- repo: /Users/wangguanran/OpenTeam/team-os
- workspace_root: /Users/wangguanran/.teamos/workspace
- git_sha: 82389d7ea29e

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

## Controls (PASS/FAIL/WAIVED)

- teamos doctor: PASS  (OAuth/gh/control-plane/repo purity/workspace checks)
- policy check: PASS  (secrets filename policy + repo/workspace governance)
- unit tests: PASS  (python3 -m unittest -q)
- requirements verify: PASS  (Raw-First drift/conflict verify (scope=teamos))
- prompt compile (dry-run): PASS  (deterministic prompt compiler (scope=teamos))
- self-improve daemon status: PASS  (daemon status/state readable (leader-only writes))

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
workspace_root=/Users/wangguanran/.teamos/workspace
workspace: OK
repo: OK
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
Ran 11 tests in 0.160s

OK
```

### req_verify

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/scripts/pipelines/requirements_raw_first.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /Users/wangguanran/.teamos/workspace verify --scope teamos`
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
  "_generated_at": "2026-02-17T01:12:44Z"
}
```

### prompt_compile

- cmd: `/Library/Developer/CommandLineTools/usr/bin/python3 /Users/wangguanran/OpenTeam/team-os/scripts/pipelines/prompt_compile.py --repo-root /Users/wangguanran/OpenTeam/team-os --workspace-root /Users/wangguanran/.teamos/workspace --scope teamos --dry-run`
- rc: 0

```text
{
  "ok": true,
  "scope": "teamos",
  "project_id": "teamos",
  "changed": true,
  "master_prompt_path": "/Users/wangguanran/OpenTeam/team-os/prompt-library/teamos/MASTER_PROMPT.md",
  "manifest_path": "/Users/wangguanran/OpenTeam/team-os/prompt-library/teamos/prompt_manifest.json"
}
```

### daemon_status

- cmd: `/Users/wangguanran/OpenTeam/team-os/teamos daemon status`
- rc: 0

```text
          "- WOULD_CREATE_OR_UPDATE TASK TEAMOS-CLUSTER-0003 TODO",
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
          "- WOULD_CREATE_OR_UPDATE MILESTONE MILESTONE:MS-CLUSTER-001 TODO"
        ]
      },
      "proposal_path": "",
      "ts": "2026-02-17T00:55:07Z",
      "wrote_truth": false
    },
    "leader": {
      "base_url": "http://127.0.0.1:8787",
      "instance_id": "61e7d5c6-7d5c-43fd-96f0-edf6f11a97cc",
      "is_leader": true,
      "leader_instance_id": "61e7d5c6-7d5c-43fd-96f0-edf6f11a97cc",
      "ok": true
    },
    "next_run_at": "2026-02-17T01:55:07Z",
    "policy_sha256": "3c3e91f2a692da01676617ee0e414e71dfeac7cbe35dd4bfe15d938ab410dd30",
    "schema_version": 1
  }
}
```
