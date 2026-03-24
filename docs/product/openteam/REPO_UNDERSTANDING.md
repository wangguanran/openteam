# Repo Understanding (Gate Artifact)

- repo: /Users/openteam-dev/Codes/openteam
- generated_at: 2026-03-09T16:39:26Z
- task_id: 
- git_sha: 6651564
- mode: upgrade

## 总体架构

- `openteam/openteam`：CLI 客户端（默认连本机 Control Plane）。
- `scaffolds/runtime/orchestrator/app/main.py`：Control Plane（FastAPI）模板代码。
- 真相源（scope=openteam）在 repo 内：`.openteam/ledger`、`.openteam/logs`、`docs/product/openteam/requirements`。
- 真相源（scope=project:<id>）必须在 Workspace（repo 外）。
- GitHub Projects v2 为视图层（mapping 在 `integrations/github_projects/mapping.yaml`）。

## 模块边界与职责

- CLI：`openteam/openteam`。
- Pipelines（本次新增）：`openteam/scripts/pipelines/`。
- Governance：`openteam/scripts/governance/`（repo purity 等）。
- Runtime/Task 入口实现：`openteam/scripts/runtime/`、`openteam/scripts/tasks/`、`openteam/scripts/issues/`、`openteam/scripts/skills/`、`openteam/scripts/policy/`。
- Requirements 协议：`openteam/scripts/requirements/` + runtime template `app/requirements_store.py`。
- Panel Sync：runtime template `app/panel_github_sync.py`（通过 Control Plane 触发）。
- Runtime 模板：`openteam/scaffolds/runtime/`（生成到 repo 外 `openteam-runtime/`）。

## 关键目录与入口

- CLI：`openteam/openteam`。
- Shell 入口：`openteam/scripts/openteam.sh` -> `openteam/openteam`。
- Pipelines：`openteam/scripts/pipelines/*.py`。
- Requirements 真相源：`openteam/docs/product/openteam/requirements/`。
- Prompt 真相源（openteam）：`openteam/specs/prompts/openteam/`。

## 构建方式

```bash
python3 -m unittest -q
./openteam --help
```

## 测试命令

```bash
python3 -m unittest -q
```

## 依赖/环境

- Python3
- pyyaml (PyYAML)
- tomli (for config parsing)

## 风险点

- Control Plane runtime 可能与 repo 模板不同步，导致 openapi 缺失端点（doctor 会失败）。
- 自我优化若以 CLI auto-wake 方式触发，可能产生非任务化写入（需要改为 daemon + leader-only）。
- 任何 project scope 真相源写入 repo 会破坏 repo purity（必须强制拦截）。

## 改动建议（最小改动策略）

- 所有真相源写入改为 pipelines 统一入口 + schema 校验。
- `openteam task close` 作为 commit/push 前闸门（tests/purity/secrets）。
- prompt/requirements/projects sync/self-improve 全部幂等化并可全量重建。

## 回滚思路

- 以 git 为回滚机制：revert 单个 task 分支的 merge/commit。
- truth-source 文件由 pipelines 生成，必要时可用 rebuild/compile 重新生成。

## 证据（必须可复现）

### tree/ls

```text
$ ls -la

total 344
drwxr-xr-x  21 openteam-dev  staff     672 Mar 10 00:13 .
drwxr-xr-x  12 openteam-dev  staff     384 Mar 10 00:00 ..
-rw-r--r--@  1 openteam-dev  staff    6148 Feb 28 21:13 .DS_Store
-rw-r--r--@  1 openteam-dev  staff     181 Mar  7 21:13 .dockerignore
drwxr-xr-x  17 openteam-dev  staff     544 Mar 10 00:35 .git
drwxr-xr-x@  3 openteam-dev  staff      96 Mar  6 11:45 .github
-rw-r--r--@  1 openteam-dev  staff    1588 Mar 10 00:29 .gitignore
drwxr-xr-x@  3 openteam-dev  staff      96 Mar  7 09:34 .openteam
-rw-r--r--@  1 openteam-dev  staff    5927 Mar 10 00:29 README.md
-rw-r--r--@  1 openteam-dev  staff    6978 Mar 10 00:29 OPENTEAM.md
drwxr-xr-x@  7 openteam-dev  staff     224 Mar 10 00:27 docs
drwxr-xr-x  11 openteam-dev  staff     352 Mar  9 23:57 evals
drwxr-xr-x   3 openteam-dev  staff      96 Feb 28 16:05 integrations
-rwxr-xr-x   1 openteam-dev  staff     486 Feb 28 16:05 run.sh
drwxr-xr-x@  4 openteam-dev  staff     128 Mar  9 23:50 scaffolds
drwxr-xr-x  29 openteam-dev  staff     928 Mar 10 00:38 scripts
drwxr-xr-x@  7 openteam-dev  staff     224 Mar  9 23:50 specs
-rwxr-xr-x@  1 openteam-dev  staff  135888 Mar 10 00:07 openteam
drwxr-xr-x   4 openteam-dev  staff     128 Mar  9 23:50 templates
drwxr-xr-x  31 openteam-dev  staff     992 Mar 10 00:07 tests
drwxr-xr-x@  5 openteam-dev  staff     160 Mar 10 00:06 tooling



$ find . -maxdepth 2 -type d (selected)

.
./.git
./.git/gk
./.git/hooks
./.git/info
./.git/logs
./.git/objects
./.git/refs
./.git/worktrees
./.github
./.github/workflows
./.openteam
./docs
./docs/archive
./docs/audits
./docs/plans
./docs/product
./docs/runbooks
./evals
./integrations
./integrations/github_projects
./scaffolds
./scaffolds/hub
./scaffolds/runtime
./scripts
./scripts/__pycache__
./scripts/cluster
./scripts/governance
./scripts/issues
./scripts/metrics
./scripts/migrations
./scripts/pipelines
./scripts/policy
./scripts/requirements
./scripts/resources
./scripts/runtime
./scripts/skills
./scripts/tasks
./specs
./specs/policies
./specs/prompts
./specs/roles
./specs/schemas
./specs/workflows
./templates
./templates/content
./templates/tasks
./tests
./tests/__pycache__
./tooling
./tooling/cluster
./tooling/docker
./tooling/migrations
```

### rg

```text
$ rg -n "@app.(get|post)\(\"/v1/" scaffolds/runtime/orchestrator/app/main.py | head

1308:@app.get("/v1/status")
1407:@app.get("/v1/agents")
1417:@app.get("/v1/tasks")
1440:@app.get("/v1/runs")
1445:@app.get("/v1/runs/{run_id}")
1453:@app.post("/v1/runs/start")
1489:@app.get("/v1/focus")
1494:@app.post("/v1/focus")
1508:@app.get("/v1/auth/status")
1517:@app.get("/v1/panel/github/config")
1547:@app.get("/v1/panel/github/health")
1600:@app.post("/v1/panel/github/sync")
1661:@app.post("/v1/chat")
1757:@app.get("/v1/requirements/show")
1791:@app.post("/v1/requirements/verify")
1798:@app.post("/v1/requirements/rebuild")
1806:@app.get("/v1/requirements/baseline/show")
1828:@app.post("/v1/requirements/baseline/set-v2")
1852:@app.post("/v1/requirements/add")
1871:@app.post("/v1/requirements/import")
1891:@app.post("/v1/requirements")
1909:@app.get("/v1/requirements")
1920:@app.get("/v1/hub/status")
1949:@app.get("/v1/hub/migrations")
1955:@app.get("/v1/hub/locks")
1961:@app.get("/v1/hub/approvals")
1975:@app.get("/v1/nodes")
1980:@app.post("/v1/nodes/register")
2020:@app.post("/v1/nodes/heartbeat")
2040:@app.get("/v1/cluster/status")
2074:@app.post("/v1/cluster/elect/attempt")
2092:@app.post("/v1/tasks/new")
2171:@app.post("/v1/recovery/scan")
2251:@app.post("/v1/recovery/resume")
2375:@app.post("/v1/self_upgrade/run")
2395:@app.get("/v1/improvement/targets")
2401:@app.post("/v1/improvement/targets")
2415:@app.get("/v1/self_upgrade/proposals")
2426:@app.post("/v1/self_upgrade/proposals/decide")
2452:@app.post("/v1/self_upgrade/discussions/sync")



$ rg -n "cmd_task_new|cmd_req_add|_auto_wake_self_improve" openteam

1212:def cmd_req_add(args: argparse.Namespace) -> None:
2540:def cmd_task_new(args: argparse.Namespace) -> None:
2906:    tn.set_defaults(fn=cmd_task_new)
2944:    ra.set_defaults(fn=cmd_req_add)
```

### build/test scripts

```text
$ ls -la scripts

total 176
drwxr-xr-x  29 openteam-dev  staff    928 Mar 10 00:38 .
drwxr-xr-x  21 openteam-dev  staff    672 Mar 10 00:13 ..
drwxr-xr-x   4 openteam-dev  staff    128 Mar  7 00:57 __pycache__
-rwxr-xr-x   1 openteam-dev  staff    905 Feb 28 16:05 _common.sh
-rwxr-xr-x@  1 openteam-dev  staff  32536 Mar 10 00:07 bootstrap_and_run.py
drwxr-xr-x   5 openteam-dev  staff    160 Feb 28 16:05 cluster
-rwxr-xr-x@  1 openteam-dev  staff    139 Mar 10 00:38 doctor.sh
drwxr-xr-x   4 openteam-dev  staff    128 Mar 10 00:07 governance
drwxr-xr-x@  3 openteam-dev  staff     96 Mar 10 00:37 issues
drwxr-xr-x   5 openteam-dev  staff    160 Feb 28 16:05 metrics
drwxr-xr-x   3 openteam-dev  staff     96 Feb 28 16:05 migrations
-rwxr-xr-x@  1 openteam-dev  staff    134 Mar 10 00:38 new_task.sh
-rwxr-xr-x@  1 openteam-dev  staff    136 Mar 10 00:38 open_issue.sh
drwxr-xr-x  47 openteam-dev  staff   1504 Mar 10 00:07 pipelines
drwxr-xr-x@  3 openteam-dev  staff     96 Mar 10 00:37 policy
-rwxr-xr-x   1 openteam-dev  staff   7608 Feb 28 16:05 policy_check.py
-rwxr-xr-x@  1 openteam-dev  staff    137 Mar 10 00:38 policy_check.sh
drwxr-xr-x  12 openteam-dev  staff    384 Mar  9 23:57 requirements
drwxr-xr-x   3 openteam-dev  staff     96 Feb 28 16:05 resources
-rwxr-xr-x@  1 openteam-dev  staff    136 Mar 10 00:38 retro.sh
drwxr-xr-x@  7 openteam-dev  staff    224 Mar 10 00:37 runtime
-rwxr-xr-x@  1 openteam-dev  staff    137 Mar 10 00:38 runtime_init.sh
-rwxr-xr-x@  1 openteam-dev  staff    140 Mar 10 00:38 runtime_secrets.sh
-rwxr-xr-x@  1 openteam-dev  staff    141 Mar 10 00:38 runtime_up_image.sh
-rwxr-xr-x@  1 openteam-dev  staff    145 Mar 10 00:38 self_improve.sh
-rwxr-xr-x@  1 openteam-dev  staff    136 Mar 10 00:38 skill_boot.sh
drwxr-xr-x@  3 openteam-dev  staff     96 Mar 10 00:37 skills
drwxr-xr-x@  4 openteam-dev  staff    128 Mar 10 00:37 tasks
-rwxr-xr-x   1 openteam-dev  staff    120 Feb 28 16:05 openteam.sh



$ ls -la scripts/pipelines

total 832
drwxr-xr-x  47 openteam-dev  staff   1504 Mar 10 00:07 .
drwxr-xr-x  29 openteam-dev  staff    928 Mar 10 00:38 ..
drwx------@  4 openteam-dev  staff    128 Mar  6 17:12 __pycache__
-rw-r--r--@  1 openteam-dev  staff  11211 Mar  9 18:07 _common.py
-rw-r--r--   1 openteam-dev  staff   1776 Feb 28 16:05 _db.py
-rw-r--r--@  1 openteam-dev  staff  22816 Mar 10 00:07 approvals.py
-rw-r--r--   1 openteam-dev  staff  18011 Feb 28 16:05 audit_deterministic_gov.py
-rw-r--r--@  1 openteam-dev  staff  12747 Mar 10 00:07 audit_execution_strategy.py
-rw-r--r--@  1 openteam-dev  staff  16157 Mar  9 23:59 audit_reqv3_locks.py
-rwxr-xr-x   1 openteam-dev  staff   1156 Feb 28 16:05 cli_project_repl.py
-rw-r--r--@  1 openteam-dev  staff   3193 Mar  9 23:59 cluster_election.py
-rwxr-xr-x   1 openteam-dev  staff   1544 Feb 28 16:05 context_detect.py
-rw-r--r--@  1 openteam-dev  staff   7668 Mar 10 00:07 db_migrate.py
-rw-r--r--@  1 openteam-dev  staff  13011 Mar  7 21:05 doctor.py
-rw-r--r--@  1 openteam-dev  staff   6437 Mar  9 23:59 feasibility_assess.py
-rwxr-xr-x   1 openteam-dev  staff   1867 Feb 28 16:05 hub_backup.py
-rwxr-xr-x@  1 openteam-dev  staff   8914 Mar  9 23:52 hub_common.py
-rwxr-xr-x   1 openteam-dev  staff   1111 Feb 28 16:05 hub_down.py
-rwxr-xr-x   1 openteam-dev  staff   1768 Feb 28 16:05 hub_export_config.py
-rwxr-xr-x   1 openteam-dev  staff   4982 Feb 28 16:05 hub_expose.py
-rwxr-xr-x   1 openteam-dev  staff   3490 Feb 28 16:05 hub_init.py
-rwxr-xr-x   1 openteam-dev  staff   1275 Feb 28 16:05 hub_logs.py
-rwxr-xr-x@  1 openteam-dev  staff   2745 Mar 10 00:07 hub_migrate.py
-rwxr-xr-x@  1 openteam-dev  staff   9776 Mar 10 00:07 hub_push_config.py
-rwxr-xr-x   1 openteam-dev  staff   1892 Feb 28 16:05 hub_restore.py
-rwxr-xr-x   1 openteam-dev  staff   1904 Feb 28 16:05 hub_status.py
-rwxr-xr-x   1 openteam-dev  staff   1114 Feb 28 16:05 hub_up.py
-rw-r--r--@  1 openteam-dev  staff  13005 Mar 10 00:07 installer_failure_classifier.py
-rwxr-xr-x@  1 openteam-dev  staff   5451 Mar 10 00:07 installer_knowledge.py
-rw-r--r--   1 openteam-dev  staff  17435 Feb 28 16:05 locks.py
-rw-r--r--@  1 openteam-dev  staff   8385 Mar  9 23:56 project_agents_inject.py
-rw-r--r--@  1 openteam-dev  staff   6423 Mar  9 23:59 project_config.py
-rw-r--r--   1 openteam-dev  staff   1865 Feb 28 16:05 projects_sync.py
-rw-r--r--@  1 openteam-dev  staff   9572 Mar 10 00:31 prompt_compile.py
-rw-r--r--@  1 openteam-dev  staff   3338 Mar  9 23:56 prompt_diff.py
-rwxr-xr-x   1 openteam-dev  staff   2491 Feb 28 16:05 remote_node_bootstrap.py
-rw-r--r--@  1 openteam-dev  staff   1944 Mar 10 00:29 repo_inspect.py
-rw-r--r--   1 openteam-dev  staff   1557 Feb 28 16:05 repo_purity_check.py
-rw-r--r--@  1 openteam-dev  staff   7086 Mar 10 00:38 repo_understanding_gate.py
-rw-r--r--@  1 openteam-dev  staff  13541 Mar  9 23:59 requirements_raw_first.py
-rwxr-xr-x   1 openteam-dev  staff   2448 Feb 28 16:05 runtime_root.py
-rw-r--r--@  1 openteam-dev  staff  33382 Mar 10 00:07 self_improve_daemon.py
-rw-r--r--@  1 openteam-dev  staff   4960 Mar  9 23:59 system_requirements_update.py
-rw-r--r--@  1 openteam-dev  staff  10330 Mar  9 23:59 task_close.py
-rw-r--r--@  1 openteam-dev  staff   7522 Mar  9 23:59 task_create.py
-rw-r--r--@  1 openteam-dev  staff  14004 Mar  9 23:59 task_ship.py
-rw-r--r--   1 openteam-dev  staff   4222 Feb 28 16:05 workspace_doctor.py
```
