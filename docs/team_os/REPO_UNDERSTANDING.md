# Repo Understanding (Gate Artifact)

- repo: /Users/wangguanran/OpenTeam/team-os
- generated_at: 2026-02-16T23:56:00Z
- task_id: TASK-20260216-233035
- git_sha: 9b78e8c
- mode: upgrade

## 总体架构

- `team-os/teamos`：CLI 客户端（默认连本机 Control Plane）。
- `templates/runtime/orchestrator/app/main.py`：Control Plane（FastAPI）模板代码。
- 真相源（scope=teamos）在 repo 内：`.team-os/ledger`、`.team-os/logs`、`docs/teamos/requirements`。
- 真相源（scope=project:<id>）必须在 Workspace（repo 外）。
- GitHub Projects v2 为视图层（mapping 在 `integrations/github_projects/mapping.yaml`）。

## 模块边界与职责

- CLI：`team-os/teamos`。
- Pipelines（本次新增）：`team-os/scripts/pipelines/`。
- Governance：`team-os/scripts/governance/`（repo purity 等）。
- Requirements 协议：`team-os/scripts/requirements/` + runtime template `app/requirements_store.py`。
- Panel Sync：runtime template `app/panel_github_sync.py`（通过 Control Plane 触发）。
- Runtime 模板：`team-os/templates/runtime/`（生成到 repo 外 `team-os-runtime/`）。

## 关键目录与入口

- CLI：`team-os/teamos`。
- Shell 入口：`team-os/scripts/teamos.sh` -> `team-os/scripts/teamos.sh`。
- Pipelines：`team-os/scripts/pipelines/*.py`。
- Requirements 真相源：`team-os/docs/teamos/requirements/`。
- Prompt 真相源（teamos）：`team-os/prompt-library/teamos/`。

## 构建方式

```bash
python3 -m unittest -q
./teamos --help
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
- `teamos task close` 作为 commit/push 前闸门（tests/purity/secrets）。
- prompt/requirements/projects sync/self-improve 全部幂等化并可全量重建。

## 回滚思路

- 以 git 为回滚机制：revert 单个 task 分支的 merge/commit。
- truth-source 文件由 pipelines 生成，必要时可用 rebuild/compile 重新生成。

## 证据（必须可复现）

### tree/ls

```text
$ ls -la

total 208
drwxr-xr-x  15 wangguanran  staff    480 Feb 16 14:36 .
drwxr-xr-x   5 wangguanran  staff    160 Feb 17 00:56 ..
-rw-r--r--@  1 wangguanran  staff   6148 Feb 16 14:36 .DS_Store
drwxr-xr-x  13 wangguanran  staff    416 Feb 17 07:30 .git
-rw-r--r--   1 wangguanran  staff   1401 Feb 17 02:04 .gitignore
drwxr-xr-x@ 15 wangguanran  staff    480 Feb 16 07:50 .team-os
-rw-r--r--   1 wangguanran  staff   5851 Feb 17 01:40 AGENTS.md
-rw-r--r--   1 wangguanran  staff   4656 Feb 16 23:50 README.md
-rw-r--r--   1 wangguanran  staff   6703 Feb 16 23:51 TEAMOS.md
drwxr-xr-x@ 12 wangguanran  staff    384 Feb 17 00:31 docs
drwxr-xr-x   9 wangguanran  staff    288 Feb 17 01:39 evals
drwxr-xr-x   5 wangguanran  staff    160 Feb 17 07:55 prompt-library
drwxr-xr-x   4 wangguanran  staff    128 Feb 16 08:21 scripts
-rwxr-xr-x   1 wangguanran  staff  67854 Feb 17 07:52 teamos
drwxr-xr-x   4 wangguanran  staff    128 Feb 16 07:48 tests



$ find . -maxdepth 2 -type d (selected)

.
./.git
./.git/hooks
./.git/info
./.git/logs
./.git/objects
./.git/refs
./.team-os
./cluster
./integrations
./.team-os/kb
./.team-os/ledger
./.team-os/logs
./.team-os/memory
./policies
./roles
./schemas
./scripts
./.team-os/state
./templates
./workflows
./docs
./docs/audits
./docs/plan
./docs/teamos
./evals
./prompt-library
./prompt-library/teamos
./scripts
./scripts/cluster
./tests
```

### rg

```text
$ rg -n "@app.(get|post)\(\"/v1/" templates/runtime/orchestrator/app/main.py | head

522:@app.get("/v1/status")
549:@app.get("/v1/agents")
559:@app.get("/v1/tasks")
582:@app.get("/v1/focus")
587:@app.post("/v1/focus")
601:@app.get("/v1/auth/status")
610:@app.get("/v1/panel/github/config")
640:@app.get("/v1/panel/github/health")
693:@app.post("/v1/panel/github/sync")
752:@app.post("/v1/chat")
849:@app.get("/v1/requirements/show")
883:@app.post("/v1/requirements/verify")
890:@app.post("/v1/requirements/rebuild")
898:@app.get("/v1/requirements/baseline/show")
920:@app.post("/v1/requirements/baseline/set-v2")
944:@app.post("/v1/requirements/add")
963:@app.post("/v1/requirements/import")
983:@app.post("/v1/requirements")
1001:@app.get("/v1/requirements")
1012:@app.get("/v1/nodes")
1017:@app.post("/v1/nodes/register")
1057:@app.post("/v1/nodes/heartbeat")
1077:@app.get("/v1/cluster/status")
1101:@app.post("/v1/cluster/elect/attempt")
1221:@app.post("/v1/tasks/new")
1256:@app.post("/v1/recovery/scan")
1273:@app.post("/v1/recovery/resume")
1293:@app.post("/v1/self_improve/run")
1338:@app.get("/v1/events/stream")



$ rg -n "cmd_task_new|cmd_req_add|_auto_wake_self_improve" teamos

518:def _should_auto_wake_self_improve(repo_root: Path, *, debounce_hours: int = 6) -> bool:
532:def _auto_wake_self_improve(args: argparse.Namespace) -> None:
852:def cmd_req_add(args: argparse.Namespace) -> None:
1445:def cmd_task_new(args: argparse.Namespace) -> None:
1657:    tn.set_defaults(fn=cmd_task_new)
1687:    ra.set_defaults(fn=cmd_req_add)
1765:        _auto_wake_self_improve(args)
```

### build/test scripts

```text
$ ls -la scripts

total 104
drwxr-xr-x  20 wangguanran  staff   640 Feb 17 07:41 .
drwxr-xr-x@ 15 wangguanran  staff   480 Feb 16 07:50 ..
-rwxr-xr-x   1 wangguanran  staff   918 Feb 14 23:39 _common.sh
-rwxr-xr-x   1 wangguanran  staff  2210 Feb 16 23:56 doctor.sh
drwxr-xr-x   4 wangguanran  staff   128 Feb 16 23:16 governance
drwxr-xr-x   5 wangguanran  staff   160 Feb 16 07:50 metrics
drwxr-xr-x   3 wangguanran  staff    96 Feb 16 08:15 migrations
-rwxr-xr-x   1 wangguanran  staff  3318 Feb 16 23:43 new_task.sh
-rwxr-xr-x   1 wangguanran  staff  1729 Feb 14 23:39 open_issue.sh
drwxr-xr-x  14 wangguanran  staff   448 Feb 17 07:48 pipelines
-rwxr-xr-x   1 wangguanran  staff  6662 Feb 16 23:44 policy_check.py
-rwxr-xr-x   1 wangguanran  staff   158 Feb 16 20:58 policy_check.sh
drwxr-xr-x  12 wangguanran  staff   384 Feb 17 01:38 requirements
drwxr-xr-x   3 wangguanran  staff    96 Feb 16 07:57 resources
-rwxr-xr-x   1 wangguanran  staff   758 Feb 14 23:39 retro.sh
-rwxr-xr-x   1 wangguanran  staff  2090 Feb 15 15:20 runtime_init.sh
-rwxr-xr-x   1 wangguanran  staff  2628 Feb 15 15:21 runtime_secrets.sh
-rwxr-xr-x   1 wangguanran  staff   752 Feb 14 23:39 self_improve.sh
-rwxr-xr-x   1 wangguanran  staff  2411 Feb 14 23:39 skill_boot.sh
-rwxr-xr-x   1 wangguanran  staff  2117 Feb 16 22:59 teamos.sh



$ ls -la scripts/pipelines

total 176
drwxr-xr-x  14 wangguanran  staff    448 Feb 17 07:48 .
drwxr-xr-x  20 wangguanran  staff    640 Feb 17 07:41 ..
-rw-r--r--   1 wangguanran  staff  10128 Feb 17 07:41 _common.py
-rw-r--r--   1 wangguanran  staff   7467 Feb 17 07:45 doctor.py
-rw-r--r--   1 wangguanran  staff   1865 Feb 17 07:47 projects_sync.py
-rw-r--r--   1 wangguanran  staff   7787 Feb 17 07:46 prompt_compile.py
-rw-r--r--   1 wangguanran  staff   1938 Feb 17 07:47 repo_inspect.py
-rw-r--r--   1 wangguanran  staff   1532 Feb 17 07:41 repo_purity_check.py
-rw-r--r--   1 wangguanran  staff   7013 Feb 17 07:48 repo_understanding_gate.py
-rw-r--r--   1 wangguanran  staff   6457 Feb 17 07:45 requirements_raw_first.py
-rw-r--r--   1 wangguanran  staff   1770 Feb 17 07:48 self_improve_daemon.py
-rw-r--r--   1 wangguanran  staff   8837 Feb 17 07:44 task_close.py
-rw-r--r--   1 wangguanran  staff   6944 Feb 17 07:43 task_create.py
-rw-r--r--   1 wangguanran  staff   4222 Feb 17 07:42 workspace_doctor.py
```
