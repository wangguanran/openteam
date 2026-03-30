# Single-Node OpenTeam Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove hub, cluster, and node infrastructure from the repository, then make OpenTeam start as a single-node local system that preserves the current delivery-studio workflow subset.

**Architecture:** Keep the local control plane and CLI, but remove the entire Docker/Postgres/Redis hub path and all multi-node control surfaces. Single-node startup will rely on local runtime layout plus `runtime.db`, with `delivery-studio` continuing to use workspace-backed artifacts and the local control plane.

**Tech Stack:** Python 3, FastAPI, Textual CLI shell, SQLite runtime DB, unittest, Ruff

---

## File Structure / Ownership Map

### Runtime / Bootstrap

- Modify: `scripts/bootstrap_and_run.py`
  - Remove hub initialization, Docker compose orchestration, Redis/Postgres bootstrap wiring, and hub status from startup/status/stop flows.
- Modify: `scripts/pipelines/doctor.py`
  - Remove hub/cluster API coverage requirements and single-node startup false negatives.
- Modify: `run.sh`
  - Preserve the same command surface but point at the simplified single-node bootstrap behavior.

### CLI surface

- Modify: `openteam_cli/__init__.py`
  - Remove `hub`, `cluster`, and `node` parser trees.
- Delete: `openteam_cli/hub.py`
- Delete: `openteam_cli/cluster.py`

### Control plane

- Modify: `scaffolds/runtime/orchestrator/app/main.py`
  - Remove hub, cluster, and node endpoints plus single-node status fields that still report distributed infrastructure.
- Delete: `scaffolds/runtime/orchestrator/app/cluster_manager.py`
- Delete: `scaffolds/runtime/orchestrator/app/redis_bus.py`
- Modify: `scaffolds/runtime/orchestrator/app/orchestrator.py`
  - Remove Redis event publishing.
- Modify: `scaffolds/runtime/orchestrator/app/observability.py`
  - Remove Redis event publishing.
- Modify: `scaffolds/runtime/orchestrator/app/domains/team_workflow/task_runtime.py`
  - Remove cluster leader checks and cluster-config imports.

### Pipelines and scripts

- Delete: `scripts/pipelines/hub_backup.py`
- Delete: `scripts/pipelines/hub_common.py`
- Delete: `scripts/pipelines/hub_down.py`
- Delete: `scripts/pipelines/hub_export_config.py`
- Delete: `scripts/pipelines/hub_expose.py`
- Delete: `scripts/pipelines/hub_init.py`
- Delete: `scripts/pipelines/hub_logs.py`
- Delete: `scripts/pipelines/hub_migrate.py`
- Delete: `scripts/pipelines/hub_push_config.py`
- Delete: `scripts/pipelines/hub_restore.py`
- Delete: `scripts/pipelines/hub_status.py`
- Delete: `scripts/pipelines/hub_up.py`
- Delete: `scripts/pipelines/cluster_election.py`
- Delete: `scripts/cluster/bootstrap_remote_node.sh`
- Delete: `scripts/cluster/join_node.sh`
- Delete: `scripts/cluster/print_join_oneliner.sh`

### Documentation

- Modify: `README.md`
- Modify: `docs/runbooks/EXECUTION_RUNBOOK.md`
- Modify: `docs/product/GOVERNANCE.md`
- Modify: `docs/product/SECURITY.md`
- Modify: `docs/product/openteam/REPO_UNDERSTANDING.md`
- Modify: `docs/runbooks/DELIVERY_STUDIO.md`

### Tests

- Modify: `tests/test_bootstrap_and_run.py`
- Modify: `tests/test_runtime_healthz.py`
- Modify: `tests/test_ci_workflows.py`
- Add: `tests/test_single_node_startup.py`
- Keep green: `tests/test_delivery_studio_runtime.py`
- Keep green: `tests/test_delivery_studio_review_gate.py`
- Keep green: `tests/test_cockpit_state.py`

---

### Task 1: Rewrite bootstrap for single-node startup

**Files:**
- Modify: `scripts/bootstrap_and_run.py`
- Modify: `run.sh`
- Test: `tests/test_bootstrap_and_run.py`
- Add: `tests/test_single_node_startup.py`

- [ ] **Step 1: Write the failing bootstrap tests for single-node startup**

```python
def test_start_flow_uses_single_node_runtime_without_hub_calls(self):
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        runtime_root = Path(td) / "openteam-runtime"
        workspace_root = runtime_root / "workspace"
        repo.mkdir(parents=True, exist_ok=True)
        calls: list[str] = []

        def fake_purity(_repo, _ws):
            calls.append("purity")
            return {"ok": True}

        def fake_layout(_rt):
            calls.append("layout")

        def fake_llm(*args, **kwargs):
            _ = args, kwargs
            calls.append("llm")
            return {"ok": True, "auth_strategy": "codex_oauth", "model": "openai/codex"}

        def fake_local_db(_runtime_root):
            calls.append("local_db")
            return {"ok": True, "path": str(_runtime_root / "state" / "runtime.db")}

        def fake_cp(*args, **kwargs):
            _ = args, kwargs
            calls.append("control_plane")
            return {"ok": True, "pid": 2222}

        def fake_crewai(*args, **kwargs):
            _ = args, kwargs
            calls.append("crewai_ready")
            return {"ok": True}

        def fake_bootstrap(*args, **kwargs):
            _ = args, kwargs
            calls.append("team_bootstrap")
            return {"ok": True}

        def fake_state(*args, **kwargs):
            _ = args, kwargs
            return {"last_run": {"ts": "2026-03-30T00:00:00Z", "status": "DONE"}}

        def fake_resume(*args, **kwargs):
            _ = args, kwargs
            calls.append("resume")
            return {"ok": True, "resumed": []}

        def fake_snapshot(*args, **kwargs):
            _ = args, kwargs
            calls.append("snapshot")
            return {"ok": True}

        with mock.patch.object(self.mod, "_check_repo_purity", side_effect=fake_purity), \
             mock.patch.object(self.mod, "_ensure_runtime_layout", side_effect=fake_layout), \
             mock.patch.object(self.mod, "_require_llm_config", side_effect=fake_llm), \
             mock.patch.object(self.mod, "_ensure_local_runtime_db", side_effect=fake_local_db), \
             mock.patch.object(self.mod, "_start_control_plane", side_effect=fake_cp), \
             mock.patch.object(self.mod, "_ensure_crewai_ready", side_effect=fake_crewai), \
             mock.patch.object(self.mod, "_run_default_team_bootstrap", side_effect=fake_bootstrap), \
             mock.patch.object(self.mod, "_read_default_team_state", side_effect=fake_state), \
             mock.patch.object(self.mod, "_resume_tasks", side_effect=fake_resume), \
             mock.patch.object(self.mod, "_status_snapshot", side_effect=fake_snapshot):
            out = self.mod._start_flow(repo, runtime_root, workspace_root, port=8787)

        self.assertTrue(bool(out.get("ok")))
        self.assertEqual(
            calls,
            ["purity", "layout", "llm", "local_db", "control_plane", "crewai_ready", "team_bootstrap", "resume", "snapshot"],
        )


def test_status_snapshot_no_longer_reports_hub(self):
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        runtime_root = Path(td) / "runtime"
        workspace_root = runtime_root / "workspace"
        repo.mkdir(parents=True, exist_ok=True)
        runtime_root.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(self.mod, "_pid_alive", return_value=False), \
             mock.patch.object(self.mod, "_llm_config", return_value={"ok": True, "model": "openai/codex"}), \
             mock.patch.object(self.mod, "_read_default_team_state", return_value={}):
            out = self.mod._status_snapshot(repo, runtime_root, workspace_root, "http://127.0.0.1:8787")

        self.assertNotIn("hub", out)
        self.assertEqual(out["control_plane"]["running"], False)
```

- [ ] **Step 2: Run bootstrap tests to verify they fail on current hub-based flow**

Run: `python3 -m unittest tests.test_bootstrap_and_run tests.test_single_node_startup -v`  
Expected: FAIL because `_start_flow()` still calls `hub_init`, `hub_up`, `hub_health`, `hub_migrate`, and `_status_snapshot()` still returns a `hub` section.

- [ ] **Step 3: Implement the single-node bootstrap path**

```python
def _ensure_local_runtime_db(runtime_root: Path) -> dict[str, Any]:
    db_path = runtime_root / "state" / "runtime.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("CREATE TABLE IF NOT EXISTS bootstrap_probe (id INTEGER PRIMARY KEY, ts TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "path": str(db_path)}


def _status_snapshot(repo: Path, runtime_root: Path, workspace_root: Path, base_url: str) -> dict[str, Any]:
    cp_pid = _read_pid(_pid_path(runtime_root, "control_plane"))
    cp_running = _pid_alive(cp_pid)

    control: dict[str, Any] = {"running": cp_running, "pid": cp_pid, "base_url": base_url}
    if cp_running:
        try:
            control["healthz"] = _http_json("GET", base_url + "/healthz", None, timeout_sec=3)
            control["status"] = _http_json("GET", base_url + "/v1/status", None, timeout_sec=3)
        except Exception as e:
            control["health_error"] = str(e)[:300]

    team_state = _read_default_team_state(runtime_root, base_url=base_url)
    team_last = (team_state.get("last_run") or {}) if isinstance(team_state.get("last_run"), dict) else {}

    return {
        "ok": True,
        "repo_root": str(repo),
        "runtime_root": str(runtime_root),
        "workspace_root": str(workspace_root),
        "llm": _llm_config(),
        "control_plane": control,
        "default_team": {
            "last_run": team_last,
            "state_backend": "control_plane_status",
        },
    }


def _start_flow(repo: Path, runtime_root: Path, workspace_root: Path, *, port: int) -> dict[str, Any]:
    base_url = f"http://127.0.0.1:{int(port)}"
    _append_audit(runtime_root, "bootstrap start")
    purity = _check_repo_purity(repo, workspace_root)
    _append_audit(runtime_root, "repo purity check passed")
    _ensure_runtime_layout(runtime_root)
    _append_audit(runtime_root, "runtime layout ensured")
    llm_cfg = _require_llm_config(runtime_root)
    _append_audit(runtime_root, "llm config check passed")
    local_db = _ensure_local_runtime_db(runtime_root)
    _append_audit(runtime_root, "local runtime db ready")
    python_deps = _ensure_python_dependencies(runtime_root)
    _append_audit(runtime_root, "python dependencies ready")
    control_plane = _start_control_plane(
        repo,
        runtime_root,
        workspace_root,
        base_url=base_url,
        port=port,
        db_url="",
        redis_url="",
        python_exec=str(python_deps.get("python") or sys.executable),
    )
    _append_audit(runtime_root, "control plane ready")
    crew_ready = _ensure_crewai_ready(base_url)
    _append_audit(runtime_root, "crewai orchestrator readiness check passed")
    team_bootstrap = _run_default_team_bootstrap(repo, base_url)
    _append_audit(runtime_root, "default team bootstrap run executed")
    st = _read_default_team_state(runtime_root, base_url=base_url)
    last_run = (st.get("last_run") or {}) if isinstance(st, dict) else {}
    if not str(last_run.get("ts") or "").strip():
        raise BootstrapError("team bootstrap not persisted: missing last_run.ts")
    recovered = _resume_tasks(base_url)
    _append_audit(runtime_root, "recovery resume executed")
    summary = _status_snapshot(repo, runtime_root, workspace_root, base_url)
    summary.update(
        {
            "startup": {
                "purity": purity,
                "llm": llm_cfg,
                "local_runtime_db": local_db,
                "python_dependencies": python_deps,
                "control_plane": control_plane,
                "crewai_ready": crew_ready,
                "team_bootstrap": team_bootstrap,
                "recovery": recovered,
            }
        }
    )
    _append_audit(runtime_root, "bootstrap completed")
    return summary
```

- [ ] **Step 4: Update runtime layout tests to stop expecting hub directories**

```python
def test_runtime_layout_idempotent(self):
    with tempfile.TemporaryDirectory() as td:
        rt = Path(td) / "openteam-runtime"
        self.mod._ensure_runtime_layout(rt)
        self.mod._ensure_runtime_layout(rt)
        self.assertTrue((rt / "state" / "audit").exists())
        self.assertTrue((rt / "state" / "runs").exists())
        self.assertTrue((rt / "workspace" / "projects").exists())
        self.assertTrue((rt / "workspace" / "shared" / "cache").exists())
        self.assertTrue((rt / "workspace" / "shared" / "tmp").exists())
        self.assertTrue((rt / "workspace" / "config").exists())
        self.assertFalse((rt / "hub").exists())
        self.assertTrue((rt / "tmp").exists())
        self.assertTrue((rt / "cache").exists())
```

- [ ] **Step 5: Run bootstrap tests to verify they pass**

Run: `python3 -m unittest tests.test_bootstrap_and_run tests.test_single_node_startup -v`  
Expected: PASS, and `_start_flow()` no longer depends on hub scripts or hub status.

- [ ] **Step 6: Commit bootstrap simplification**

```bash
git add \
  scripts/bootstrap_and_run.py \
  run.sh \
  tests/test_bootstrap_and_run.py \
  tests/test_single_node_startup.py
git commit -m "refactor: switch bootstrap to single-node startup"
```

### Task 2: Remove hub, cluster, and node CLI surfaces

**Files:**
- Modify: `openteam_cli/__init__.py`
- Delete: `openteam_cli/hub.py`
- Delete: `openteam_cli/cluster.py`
- Test: `tests/test_openteam_repl.py`

- [ ] **Step 1: Write the failing CLI tests for removed commands**

```python
def test_removed_hub_cluster_and_node_commands_are_absent(self) -> None:
    parser = openteam_cli.main(["cockpit", "--help"])
    self.assertEqual(parser, 0)

    with self.assertRaises(SystemExit) as hub_ctx:
        openteam_cli.main(["hub", "--help"])
    self.assertEqual(hub_ctx.exception.code, 2)

    with self.assertRaises(SystemExit) as cluster_ctx:
        openteam_cli.main(["cluster", "--help"])
    self.assertEqual(cluster_ctx.exception.code, 2)

    with self.assertRaises(SystemExit) as node_ctx:
        openteam_cli.main(["node", "--help"])
    self.assertEqual(node_ctx.exception.code, 2)
```

- [ ] **Step 2: Run CLI tests to verify they fail before parser cleanup**

Run: `python3 -m unittest tests.test_openteam_repl -v`  
Expected: FAIL because the parser still accepts `hub`, `cluster`, and `node`.

- [ ] **Step 3: Remove parser wiring and imports**

```python
from .cockpit import cmd_cockpit
from .team import cmd_team_list, cmd_team_run, cmd_team_watch, cmd_team_proposals, cmd_team_decide, cmd_team_discussions_sync, cmd_team_coding_run, cmd_team_coding_tasks, cmd_team_logs, cmd_team_bug_scan_live
from .requirements import cmd_req_add, cmd_req_import, cmd_req_list, cmd_req_conflicts, cmd_req_verify, cmd_req_rebuild, cmd_req_baseline_show, cmd_req_baseline_set_v2
from .misc import (
    cmd_chat, cmd_doctor, cmd_policy_check, cmd_db_migrate, cmd_approvals_list,
    cmd_prompt_compile, cmd_prompt_diff,
    cmd_metrics_check, cmd_metrics_analyze, cmd_metrics_bootstrap,
    cmd_audit_deterministic_gov, cmd_audit_execution_strategy, cmd_audit_reqv3_locks,
    cmd_daemon_start, cmd_daemon_stop, cmd_daemon_status,
    cmd_repo_create,
    cmd_task_new, cmd_task_close, cmd_task_ship, cmd_task_resume,
    cmd_improvement_targets, cmd_improvement_target_add,
    cmd_openclaw_status, cmd_openclaw_config, cmd_openclaw_test, cmd_openclaw_sweep,
)

cp = sp.add_parser("cockpit", help="Terminal delivery-studio cockpit")
cp.add_argument("--project", default="")
cp.add_argument("--team", default="delivery-studio")
cp.set_defaults(fn=cmd_cockpit)

rp = sp.add_parser("repo", help="Repo operations (GitHub)")
rp_sp = rp.add_subparsers(dest="subcmd", required=True)
```

- [ ] **Step 4: Delete the obsolete CLI handler modules**

```bash
rm openteam_cli/hub.py
rm openteam_cli/cluster.py
```

- [ ] **Step 5: Run CLI tests to verify parser surface is clean**

Run: `python3 -m unittest tests.test_openteam_repl -v`  
Expected: PASS, with `cockpit` still present and removed commands rejected by argparse.

- [ ] **Step 6: Commit CLI deletions**

```bash
git add openteam_cli/__init__.py tests/test_openteam_repl.py
git rm openteam_cli/hub.py openteam_cli/cluster.py
git commit -m "refactor: remove hub and cluster cli surfaces"
```

### Task 3: Remove hub and cluster endpoints from the control plane

**Files:**
- Modify: `scaffolds/runtime/orchestrator/app/main.py`
- Delete: `scaffolds/runtime/orchestrator/app/cluster_manager.py`
- Delete: `scaffolds/runtime/orchestrator/app/redis_bus.py`
- Modify: `scaffolds/runtime/orchestrator/app/orchestrator.py`
- Modify: `scaffolds/runtime/orchestrator/app/observability.py`
- Modify: `scaffolds/runtime/orchestrator/app/domains/team_workflow/task_runtime.py`
- Test: `tests/test_runtime_healthz.py`

- [ ] **Step 1: Write failing control-plane tests for single-node status and removed APIs**

```python
def test_healthz_no_longer_reports_redis_bus(self) -> None:
    os.environ["OPENTEAM_REPO_PATH"] = str(self.repo_root)
    response = app_main.Response()
    with (
        mock.patch.object(app_main.engine_runtime, "probe_crewai", return_value={"importable": True, "version": "test"}),
        mock.patch.object(app_main.DB, "list_events", return_value=[]),
    ):
        payload = app_main.healthz(response)

    self.assertEqual(payload["status"], "ok")
    self.assertNotIn("redis_bus", payload)


def test_status_no_longer_reports_redis_bus(self) -> None:
    with mock.patch.object(app_main, "_active_projects_summary", return_value=[]), \
         mock.patch.object(app_main, "_load_tasks_summary", return_value=[]), \
         mock.patch.object(app_main.DB, "list_runs", return_value=[]), \
         mock.patch.object(app_main.DB, "list_agents", return_value=[]):
        payload = app_main.v1_status()

    self.assertNotIn("redis_bus", payload)
```

- [ ] **Step 2: Run control-plane tests to verify they fail before endpoint cleanup**

Run: `python3 -m unittest tests.test_runtime_healthz -v`  
Expected: FAIL because `healthz()` and `v1_status()` still return `redis_bus`, and hub/cluster endpoints still exist in the OpenAPI surface.

- [ ] **Step 3: Remove hub/cluster imports, models, and endpoints from `main.py`**

```python
from .demo_seed import seed_mock_data
from . import github_checks_client
from .github_projects_client import GitHubAPIError, GitHubAuthError, GitHubGraphQL, RATE_LIMIT_QUERY, resolve_github_token
from .panel_github_sync import GitHubProjectsPanelSync, PanelSyncError
from .panel_mapping import PanelMappingError, load_mapping
from .plan_store import list_milestones
from . import observability
from .requirements_store import (
    RequirementsError,
    add_requirement_raw_first,
    rebuild_requirements_md,
    verify_requirements_raw_first,
    propose_baseline_v2,
)


@app.get("/healthz")
def healthz(response: Response):
    openteam_path = os.getenv("OPENTEAM_REPO_PATH", "/openteam")
    checks = _openteam_checks(openteam_path)
    crewai_info = engine_runtime.probe_crewai()
    ok = (
        checks["exists"]
        and checks["specs_workflows_dir_exists"]
        and checks["specs_roles_dir_exists"]
        and checks["runtime_role_library_exists"]
        and checks["team_specs_exist"]
        and checks["orchestrator_exists"]
        and bool(crewai_info.get("importable"))
    )
    db = {"backend": "sqlite", "ok": True, "error": ""}
    try:
        _ = DB.list_events(after_id=0, limit=1)
    except Exception as e:
        db["ok"] = False
        db["error"] = str(e)[:200]
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks, "crewai": crewai_info, "db": db}


def v1_status():
    return {
        "instance_id": instance_id,
        "default_team_id": default_team_id,
        "workspace_root": ws_root,
        "workspace_projects_count": len(_list_workspace_projects()),
        "current_focus": focus,
        "active_projects": active_projects,
        "active_runs": runs,
        "agents": agents,
        "tasks": tasks,
        "task_run_sync": task_run_sync,
        "teams": teams_payload,
        "crewai": crewai_info,
        "improvement_targets": targets,
        "improvement_target_summaries": target_summaries,
        "openclaw": openclaw_status,
        "pending_decisions": pending,
    }

# delete: /v1/hub/status
# delete: /v1/hub/migrations
# delete: /v1/hub/locks
# delete: /v1/hub/approvals
# delete: /v1/nodes
# delete: /v1/nodes/register
# delete: /v1/nodes/heartbeat
# delete: /v1/cluster/status
# delete: /v1/cluster/elect/attempt
```

- [ ] **Step 4: Remove Redis and cluster side effects from runtime modules**

```python
# scaffolds/runtime/orchestrator/app/orchestrator.py
from .state_store import openteam_root


def run_once(*, db, spec: RunSpec, actor: str = "orchestrator") -> dict[str, Any]:
    flow = crew_tools.normalize_flow(spec.flow)
    task_id = str(spec.task_id or "").strip()
    run_id_seed = f"run-{task_id}" if task_id else None
    crewai_info = engine_runtime.require_crewai_importable()
    run_id = db.upsert_run(
        run_id=run_id_seed,
        project_id=spec.project_id,
        workstream_id=spec.workstream_id,
        objective=spec.objective,
        state="RUNNING",
    )
    db.add_event(
        event_type="RUN_STARTED",
        actor=actor,
        project_id=spec.project_id,
        workstream_id=spec.workstream_id,
        payload={"run_id": run_id, "flow": flow, "task_id": task_id, "crewai": crewai_info},
    )


# scaffolds/runtime/orchestrator/app/observability.py
"""
Observability module for OpenTeam Control Plane.

Provides real-time metrics aggregation, workflow health monitoring,
and cost tracking for the single-node runtime.
"""
from .runtime_db import EventRow, RunRow, AgentRow


# scaffolds/runtime/orchestrator/app/domains/team_workflow/task_runtime.py
def _delivery_lease_settings() -> dict[str, int]:
    ttl = 600
    renew = 300
    heartbeat = max(15, min(renew, max(15, ttl // 3)))
    return {"ttl_sec": ttl, "renew_interval_sec": renew, "heartbeat_interval_sec": heartbeat}
```

- [ ] **Step 5: Delete obsolete distributed-runtime modules**

```bash
git rm scaffolds/runtime/orchestrator/app/cluster_manager.py
git rm scaffolds/runtime/orchestrator/app/redis_bus.py
```

- [ ] **Step 6: Run control-plane tests to verify single-node status shape**

Run: `python3 -m unittest tests.test_runtime_healthz tests.test_delivery_studio_runtime -v`  
Expected: PASS, with health/status endpoints reporting only local runtime concerns.

- [ ] **Step 7: Commit control-plane simplification**

```bash
git add \
  scaffolds/runtime/orchestrator/app/main.py \
  scaffolds/runtime/orchestrator/app/orchestrator.py \
  scaffolds/runtime/orchestrator/app/observability.py \
  scaffolds/runtime/orchestrator/app/domains/team_workflow/task_runtime.py \
  tests/test_runtime_healthz.py \
  tests/test_delivery_studio_runtime.py
git rm \
  scaffolds/runtime/orchestrator/app/cluster_manager.py \
  scaffolds/runtime/orchestrator/app/redis_bus.py
git commit -m "refactor: remove hub and cluster control-plane paths"
```

### Task 4: Remove hub and cluster pipelines, scripts, and CI expectations

**Files:**
- Delete: `scripts/pipelines/hub_*`
- Delete: `scripts/pipelines/cluster_election.py`
- Delete: `scripts/cluster/*`
- Modify: `scripts/pipelines/doctor.py`
- Modify: `tests/test_ci_workflows.py`

- [ ] **Step 1: Write failing doctor/API coverage tests for the single-node contract**

```python
def test_doctor_required_api_paths_match_single_node_surface(self) -> None:
    text = (ROOT / "scripts" / "pipelines" / "doctor.py").read_text(encoding="utf-8")
    self.assertNotIn('"/v1/hub/status"', text)
    self.assertNotIn('"/v1/cluster/status"', text)
    self.assertNotIn('"/v1/nodes/register"', text)
    self.assertIn('"/v1/status"', text)
    self.assertIn('"/v1/teams"', text)


def test_readme_no_longer_advertises_hub_commands(self) -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    self.assertNotIn("openteam hub init|up|status|migrate", text)
    self.assertNotIn("Hub(Postgres)", text)
```

- [ ] **Step 2: Run CI workflow tests to verify they fail before cleanup**

Run: `python3 -m unittest tests.test_ci_workflows -v`  
Expected: FAIL because doctor and docs still mention hub and cluster surfaces.

- [ ] **Step 3: Simplify doctor to single-node API coverage and readiness**

```python
required = [
    "/v1/status",
    "/v1/agents",
    "/v1/runs",
    "/v1/runs/start",
    "/v1/tasks",
    "/v1/focus",
    "/v1/chat",
    "/v1/requirements",
    "/v1/panel/github/sync",
    "/v1/panel/github/health",
    "/v1/panel/github/config",
    "/v1/tasks/new",
    "/v1/recovery/scan",
    "/v1/recovery/resume",
    "/v1/teams",
]
```

- [ ] **Step 4: Delete obsolete hub and cluster scripts**

```bash
git rm scripts/pipelines/hub_backup.py
git rm scripts/pipelines/hub_common.py
git rm scripts/pipelines/hub_down.py
git rm scripts/pipelines/hub_export_config.py
git rm scripts/pipelines/hub_expose.py
git rm scripts/pipelines/hub_init.py
git rm scripts/pipelines/hub_logs.py
git rm scripts/pipelines/hub_migrate.py
git rm scripts/pipelines/hub_push_config.py
git rm scripts/pipelines/hub_restore.py
git rm scripts/pipelines/hub_status.py
git rm scripts/pipelines/hub_up.py
git rm scripts/pipelines/cluster_election.py
git rm scripts/cluster/bootstrap_remote_node.sh
git rm scripts/cluster/join_node.sh
git rm scripts/cluster/print_join_oneliner.sh
```

- [ ] **Step 5: Run doctor and CI workflow tests again**

Run: `python3 -m unittest tests.test_ci_workflows -v`  
Expected: PASS, with no remaining single-node runtime references to hub or cluster endpoints.

- [ ] **Step 6: Commit script and doctor cleanup**

```bash
git add scripts/pipelines/doctor.py tests/test_ci_workflows.py
git rm scripts/pipelines/hub_*.py scripts/pipelines/cluster_election.py scripts/cluster/*
git commit -m "refactor: remove hub and cluster pipelines"
```

### Task 5: Update docs to the single-node product story

**Files:**
- Modify: `README.md`
- Modify: `docs/runbooks/EXECUTION_RUNBOOK.md`
- Modify: `docs/product/GOVERNANCE.md`
- Modify: `docs/product/SECURITY.md`
- Modify: `docs/product/openteam/REPO_UNDERSTANDING.md`
- Modify: `docs/runbooks/DELIVERY_STUDIO.md`

- [ ] **Step 1: Write the failing doc assertions in workflow tests**

```python
def test_readme_describes_single_node_runtime(self) -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    self.assertIn("单机", text)
    self.assertNotIn("Hub(Postgres)", text)
    self.assertNotIn("openteam hub", text)
    self.assertNotIn("openteam cluster", text)
```

- [ ] **Step 2: Run doc/CI tests to verify they fail before doc edits**

Run: `python3 -m unittest tests.test_ci_workflows -v`  
Expected: FAIL because README and runbooks still advertise hub and cluster.

- [ ] **Step 3: Rewrite docs to the single-node contract**

```text
# OpenTeam (通用 AI 开发团队操作系统)

本仓库提供一个可长期运行、可审计、可恢复的单机 OpenTeam。
默认运行形态为：

- 本机 CLI
- 本机 Control Plane
- 本地 runtime 目录
- 本地 SQLite `runtime.db`

快速开始：

git clone https://github.com/openteam-dev/openteam.git
cd openteam
codex login
export OPENTEAM_LLM_MODEL="openai/codex"
./run.sh start
./run.sh status
./run.sh doctor
```

- [ ] **Step 4: Run CI workflow tests after doc updates**

Run: `python3 -m unittest tests.test_ci_workflows -v`  
Expected: PASS, with docs aligned to the single-node runtime and no hub/cluster references in the primary product story.

- [ ] **Step 5: Commit doc cutover**

```bash
git add \
  README.md \
  docs/runbooks/EXECUTION_RUNBOOK.md \
  docs/product/GOVERNANCE.md \
  docs/product/SECURITY.md \
  docs/product/openteam/REPO_UNDERSTANDING.md \
  docs/runbooks/DELIVERY_STUDIO.md \
  tests/test_ci_workflows.py
git commit -m "docs: describe openteam as single-node runtime"
```

### Task 6: Run the full single-node verification suite

**Files:**
- Test: `tests/test_bootstrap_and_run.py`
- Test: `tests/test_runtime_healthz.py`
- Test: `tests/test_delivery_studio_runtime.py`
- Test: `tests/test_delivery_studio_review_gate.py`
- Test: `tests/test_cockpit_state.py`
- Test: `tests/test_openteam_repl.py`
- Test: `tests/test_ci_workflows.py`

- [ ] **Step 1: Run the full targeted unittest suite**

Run:

```bash
python3 -m unittest \
  tests.test_bootstrap_and_run \
  tests.test_runtime_healthz \
  tests.test_delivery_studio_runtime \
  tests.test_delivery_studio_review_gate \
  tests.test_cockpit_state \
  tests.test_openteam_repl \
  tests.test_ci_workflows \
  tests.test_single_node_startup -v
```

Expected: PASS, with no hub/cluster assumptions remaining in startup, health, CLI, or delivery-studio regressions.

- [ ] **Step 2: Run Ruff on the modified surfaces**

Run:

```bash
uvx ruff check --select E,F,W \
  scripts/bootstrap_and_run.py \
  scripts/pipelines/doctor.py \
  openteam_cli/__init__.py \
  scaffolds/runtime/orchestrator/app/main.py \
  scaffolds/runtime/orchestrator/app/orchestrator.py \
  scaffolds/runtime/orchestrator/app/observability.py \
  scaffolds/runtime/orchestrator/app/domains/team_workflow/task_runtime.py \
  tests/test_bootstrap_and_run.py \
  tests/test_runtime_healthz.py \
  tests/test_ci_workflows.py \
  tests/test_single_node_startup.py
```

Expected: `All checks passed!`

- [ ] **Step 3: Verify local startup path manually**

Run:

```bash
OPENTEAM_LLM_MODEL=openai/codex ./run.sh start
./run.sh status
./run.sh doctor
./run.sh stop
```

Expected:

- `./run.sh start` succeeds without Docker/Redis/Postgres setup
- `./run.sh status` reports `control_plane.running: true` and no `hub` section
- `./run.sh doctor` no longer requires hub or cluster paths
- `./run.sh stop` stops the local control plane cleanly

- [ ] **Step 4: Commit final verification adjustments if needed**

```bash
git add -A
git commit -m "test: verify single-node openteam cutover"
```
