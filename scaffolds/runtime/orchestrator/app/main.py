import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from agents import Agent  # OpenAI Agents SDK (placeholder; must not call models on startup)
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import codex_llm
from .demo_seed import seed_mock_data
from .github_projects_client import GitHubAPIError, GitHubAuthError, GitHubGraphQL, RATE_LIMIT_QUERY, resolve_github_token
from .panel_github_sync import GitHubProjectsPanelSync, PanelSyncError
from .panel_mapping import PanelMappingError, load_mapping
from .plan_store import list_milestones
from . import redis_bus
from .requirements_store import (
    RequirementsError,
    add_requirement_raw_first,
    rebuild_requirements_md,
    verify_requirements_raw_first,
    propose_baseline_v2,
)
from .runtime_db import RunRow, RuntimeDB
from . import cluster_manager
from . import crewai_orchestrator
from . import crewai_role_registry
from . import crewai_self_upgrade
from . import crewai_self_upgrade_delivery
from . import crewai_runtime
from . import improvement_store
from . import openclaw_reporter
from . import crew_tools
from .teams.repo_improvement.registries import workflows as repo_improvement_workflows
from .state_store import (
    StateError,
    ensure_instance_id,
    ledger_tasks_dir,
    logs_tasks_dir,
    load_focus,
    load_workstreams,
    github_projects_mapping_path,
    runtime_root,
    runtime_state_root,
    save_focus,
    team_os_root,
    teamos_requirements_dir,
)
from . import workspace_store

try:
    from .n8n_hook import emit_n8n_event  # type: ignore
except Exception:  # pragma: no cover
    def emit_n8n_event(*args, **kwargs):  # type: ignore
        _ = args, kwargs
        return None


app = FastAPI(title="Team OS Control Plane", version="0.2.0")


@app.exception_handler(workspace_store.WorkspaceError)
async def _workspace_error(_req: Request, exc: Exception):
    # Defensive: never allow project writes to land inside the team-os git repo.
    return JSONResponse(
        status_code=400,
        content={
            "error": str(exc),
            "hint": "Ensure workspace is initialized and outside the team-os repo: run `teamos workspace init` and set TEAMOS_WORKSPACE_ROOT for the control-plane.",
        },
    )


@app.exception_handler(StateError)
async def _state_error(_req: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"error": str(exc), "hint": "Check configuration and workspace. Try: teamos workspace doctor"})


@app.exception_handler(RequirementsError)
async def _requirements_error(_req: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"error": str(exc), "hint": "Requirements store error. Try: teamos req list/conflicts"})


# Minimal placeholder agent. Never call models on startup.
CONTROL_PLANE_AGENT = Agent(
    name="TeamOS-Control-Plane",
    instructions=(
        "You are the Team OS control plane. Enforce: no secrets in git; "
        "full traceability for web research; task ledger/logging; approval gates; "
        "prompt-injection defenses; requirements conflict detection."
    ),
)


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_TASK_STATE_IN_PROGRESS = frozenset({"doing", "running", "work", "in_progress", "inprogress"})
_RUN_STATE_ACTIVE = frozenset({"RUNNING", "PAUSED"})
_REPO_IMPROVEMENT_LOOP_STATE_LOCK = threading.Lock()
_REPO_IMPROVEMENT_LOOP_STATE: dict[str, dict[str, Any]] = {}
_REPO_IMPROVEMENT_LOOP_CLEANUP = "repo_improvement_cleanup"
_REPO_IMPROVEMENT_LOOP_STARTUP = "repo_improvement_startup"
_REPO_IMPROVEMENT_LOOP_DISCOVERY = "repo_improvement_discovery"
_REPO_IMPROVEMENT_LOOP_DISCUSSION = "repo_improvement_discussion"
_REPO_IMPROVEMENT_LOOP_DELIVERY = "repo_improvement_delivery"


def _normalize_task_state(state: Any) -> str:
    s = str(state or "").strip().lower()
    if s in ("running", "work", "in_progress", "inprogress", "doing"):
        return "doing"
    return s


def _normalize_run_state(state: Any) -> str:
    return str(state or "").strip().upper()


def _set_repo_improvement_loop_state(loop_id: str, **fields: Any) -> None:
    now = _utc_now_iso()
    with _REPO_IMPROVEMENT_LOOP_STATE_LOCK:
        current = dict(_REPO_IMPROVEMENT_LOOP_STATE.get(loop_id) or {})
        current.update(fields)
        current.setdefault("loop_id", loop_id)
        current["updated_at"] = now
        _REPO_IMPROVEMENT_LOOP_STATE[loop_id] = current


def _repo_improvement_loop_state_snapshot() -> dict[str, dict[str, Any]]:
    with _REPO_IMPROVEMENT_LOOP_STATE_LOCK:
        return {key: dict(value) for key, value in _REPO_IMPROVEMENT_LOOP_STATE.items()}


def _repo_improvement_workflow_status_snapshot(*, target_id: str, project_id: str) -> dict[str, dict[str, Any]]:
    lanes = {
        "bug": crewai_role_registry.WORKFLOW_BUG_FIX,
        "feature": crewai_role_registry.WORKFLOW_FEATURE_IMPROVEMENT,
        "quality": crewai_role_registry.WORKFLOW_QUALITY_IMPROVEMENT,
        "process": crewai_role_registry.WORKFLOW_PROCESS_IMPROVEMENT,
    }
    statuses: dict[str, dict[str, Any]] = {}
    for lane, workflow_id in lanes.items():
        try:
            spec = repo_improvement_workflows.workflow_spec(workflow_id, project_id=project_id)
        except Exception:
            continue
        runtime_state = (
            repo_improvement_workflows.workflow_runtime_state(target_id, workflow_id)
            if str(target_id or "").strip()
            else {}
        )
        statuses[lane] = {
            "workflow_id": workflow_id,
            "lane": lane,
            "display_name_zh": str(spec.display_name_zh or "").strip(),
            "enabled": bool(spec.enabled),
            "disabled_reason": str(spec.disabled_reason or "").strip(),
            "max_candidates": int(spec.max_candidates()),
            "cooldown_hours": int(spec.cooldown_hours()),
            "active_window_start_hour": int(spec.active_window_start_hour()),
            "active_window_end_hour": int(spec.active_window_end_hour()),
            "max_continuous_runtime_minutes": int(spec.max_continuous_runtime_minutes()),
            "dormant_after_zero_scans": int(spec.dormant_after_zero_scans()),
            "runtime_state": runtime_state,
        }
    return statuses


def _task_id_from_run_id(run_id: str) -> str:
    rid = str(run_id or "").strip()
    if not rid.startswith("run-"):
        return ""
    tail = rid[4:].strip()
    if not tail:
        return ""
    for sep in ("::", "@"):
        if sep in tail:
            return tail.split(sep, 1)[0].strip()
    return tail


def _task_id_from_run_objective(objective: Any) -> str:
    text = str(objective or "").strip()
    if not text:
        return ""
    match = re.search(r"\btask\s+([A-Za-z0-9._:-]+)\b", text, re.IGNORECASE)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _repo_improvement_task_id_for_run(run: RunRow | dict[str, Any]) -> str:
    run_id = str((run.run_id if isinstance(run, RunRow) else run.get("run_id")) or "").strip()
    task_id = _task_id_from_run_id(run_id)
    if task_id:
        return task_id
    objective = run.objective if isinstance(run, RunRow) else run.get("objective")
    return _task_id_from_run_objective(objective)


def _serialize_agent_row(agent: Any) -> dict[str, Any]:
    return {
        "agent_id": str(getattr(agent, "agent_id", "") or ""),
        "role_id": str(getattr(agent, "role_id", "") or ""),
        "project_id": str(getattr(agent, "project_id", "") or ""),
        "workstream_id": str(getattr(agent, "workstream_id", "") or ""),
        "task_id": str(getattr(agent, "task_id", "") or ""),
        "state": str(getattr(agent, "state", "") or ""),
        "started_at": str(getattr(agent, "started_at", "") or ""),
        "last_heartbeat": str(getattr(agent, "last_heartbeat", "") or ""),
        "current_action": str(getattr(agent, "current_action", "") or ""),
    }


def _repo_improvement_agents_for_run(run: RunRow) -> list[dict[str, Any]]:
    task_id = _repo_improvement_task_id_for_run(run)
    rows = DB.list_agents(project_id=str(run.project_id or ""), workstream_id=str(run.workstream_id or ""))
    out: list[dict[str, Any]] = []
    for row in rows:
        if task_id:
            if str(row.task_id or "").strip() != task_id:
                continue
        else:
            if str(row.task_id or "").strip():
                continue
            if str(row.started_at or "").strip() < str(run.started_at or "").strip():
                continue
        out.append(_serialize_agent_row(row))
    out.sort(key=lambda item: (str(item.get("started_at") or ""), str(item.get("role_id") or ""), str(item.get("agent_id") or "")))
    return out


def _repo_improvement_event_matches_run(event: Any, *, run: RunRow, task_id: str) -> bool:
    if str(getattr(event, "project_id", "") or "") != str(run.project_id or ""):
        return False
    if str(getattr(event, "workstream_id", "") or "") != str(run.workstream_id or ""):
        return False
    payload = getattr(event, "payload", {}) if isinstance(getattr(event, "payload", {}), dict) else {}
    if str(payload.get("run_id") or "").strip() == str(run.run_id or "").strip():
        return True
    if task_id and str(payload.get("task_id") or "").strip() == task_id:
        return True
    return False


def _repo_improvement_events_since(*, run: RunRow, after_id: int, limit: int = 200) -> tuple[list[dict[str, Any]], int]:
    task_id = _repo_improvement_task_id_for_run(run)
    rows: list[dict[str, Any]] = []
    cursor = max(0, int(after_id or 0))
    latest_seen = cursor
    max_rows = max(1, min(int(limit or 200), 1000))
    while len(rows) < max_rows:
        batch = DB.list_events(after_id=cursor, limit=1000)
        if not batch:
            break
        for item in batch:
            latest_seen = max(latest_seen, int(getattr(item, "id", 0) or 0))
            cursor = latest_seen
            if not _repo_improvement_event_matches_run(item, run=run, task_id=task_id):
                continue
            rows.append(
                {
                    "id": int(getattr(item, "id", 0) or 0),
                    "ts": str(getattr(item, "ts", "") or ""),
                    "event_type": str(getattr(item, "event_type", "") or ""),
                    "actor": str(getattr(item, "actor", "") or ""),
                    "project_id": str(getattr(item, "project_id", "") or ""),
                    "workstream_id": str(getattr(item, "workstream_id", "") or ""),
                    "payload": dict(getattr(item, "payload", {}) or {}),
                }
            )
            if len(rows) >= max_rows:
                break
        if len(batch) < 1000:
            break
    return rows[-max_rows:], latest_seen


def _sse_chunk(*, event: str, data: dict[str, Any], event_id: Optional[int] = None) -> bytes:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {int(event_id)}")
    if event:
        lines.append(f"event: {event}")
    encoded = json.dumps(data, ensure_ascii=False)
    for line in encoded.splitlines() or ["{}"]:
        lines.append(f"data: {line}")
    lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _task_run_sync_summary(*, tasks: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    in_progress: list[dict[str, Any]] = []
    task_keys: set[tuple[str, str, str]] = set()
    for t in tasks:
        state = _normalize_task_state(t.get("state"))
        if state not in _TASK_STATE_IN_PROGRESS:
            continue
        project_id = str(t.get("project_id") or "").strip()
        workstream_id = str(t.get("workstream_id") or "").strip()
        task_id = str(t.get("task_id") or "").strip()
        key = (project_id, workstream_id, task_id)
        task_keys.add(key)
        in_progress.append(
            {
                "task_id": task_id,
                "project_id": project_id,
                "workstream_id": workstream_id,
                "state": state,
            }
        )

    active_runs: list[dict[str, Any]] = []
    mapped_run_keys: set[tuple[str, str, str]] = set()
    unmapped_runs: list[dict[str, Any]] = []
    for r in runs:
        run_state = _normalize_run_state(r.get("state"))
        if run_state not in _RUN_STATE_ACTIVE:
            continue
        run_id = str(r.get("run_id") or "").strip()
        project_id = str(r.get("project_id") or "").strip()
        workstream_id = str(r.get("workstream_id") or "").strip()
        task_id = _task_id_from_run_id(run_id)
        row = {
            "run_id": run_id,
            "project_id": project_id,
            "workstream_id": workstream_id,
            "state": run_state,
            "task_id": task_id,
        }
        active_runs.append(row)
        if task_id:
            mapped_run_keys.add((project_id, workstream_id, task_id))
        else:
            unmapped_runs.append(
                {
                    "run_id": run_id,
                    "project_id": project_id,
                    "workstream_id": workstream_id,
                    "state": run_state,
                }
            )

    missing_runs: list[dict[str, Any]] = []
    for t in in_progress:
        key = (t["project_id"], t["workstream_id"], t["task_id"])
        if key not in mapped_run_keys:
            missing_runs.append(t)

    orphan_runs: list[dict[str, Any]] = []
    for r in active_runs:
        task_id = str(r.get("task_id") or "")
        if not task_id:
            continue
        key = (str(r.get("project_id") or ""), str(r.get("workstream_id") or ""), task_id)
        if key not in task_keys:
            orphan_runs.append(r)

    return {
        "ok": (not missing_runs and not orphan_runs),
        "in_progress_task_count": len(in_progress),
        "active_run_count": len(active_runs),
        "missing_run_for_tasks": missing_runs[:50],
        "orphan_active_runs": orphan_runs[:50],
        "unmapped_active_runs": unmapped_runs[:50],
    }


def _hub_root() -> Path:
    return (runtime_root() / "hub").resolve()


def _hub_env() -> dict[str, str]:
    p = _hub_root() / "env" / ".env"
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[str(k).strip()] = str(v).strip()
    return out


def _db_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    dsn = str(os.getenv("TEAMOS_DB_URL") or "").strip()
    if not dsn:
        return []
    try:
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore

        conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3)
    except Exception:
        return []
    try:
        with conn.cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows or []:
            d = dict(r)
            for k, v in list(d.items()):
                if hasattr(v, "isoformat"):
                    try:
                        d[k] = v.isoformat()
                    except Exception:
                        d[k] = str(v)
            out.append(d)
        return out
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _team_os_checks(team_os_path: str) -> dict[str, Any]:
    p = Path(team_os_path)
    specs_workflows_dir = p / "specs" / "workflows"
    specs_roles_dir = p / "specs" / "roles"
    state_dir = runtime_state_root()
    runtime_app_dir = p / "scaffolds" / "runtime" / "orchestrator" / "app"
    crewai_orchestrator_file = runtime_app_dir / "crewai_orchestrator.py"
    runtime_role_library_dir = runtime_app_dir / "role_library" / "specs"
    repo_improvement_team_file = runtime_app_dir / "teams" / "repo_improvement" / "specs" / "team.yaml"
    workflow_files: list[str] = []
    if specs_workflows_dir.exists():
        workflow_files = sorted(x.name for x in specs_workflows_dir.glob("*.yaml"))
    role_files: list[str] = []
    if specs_roles_dir.exists():
        role_files = sorted(
            {
                x.name
                for pattern in ("*.md", "*.yaml", "*.yml")
                for x in specs_roles_dir.glob(pattern)
            }
        )
    return {
        "team_os_path": str(p),
        "exists": p.exists(),
        "specs_workflows_dir_exists": specs_workflows_dir.exists(),
        "specs_roles_dir_exists": specs_roles_dir.exists(),
        "state_dir_exists": state_dir.exists(),
        "crewai_orchestrator_exists": crewai_orchestrator_file.exists(),
        "runtime_role_library_exists": runtime_role_library_dir.exists(),
        "repo_improvement_team_spec_exists": repo_improvement_team_file.exists(),
        # Backward-compatible aliases for older callers.
        "workflows_dir_exists": specs_workflows_dir.exists(),
        "roles_dir_exists": specs_roles_dir.exists(),
        "workflow_files": workflow_files,
        "role_files": role_files,
    }


def _db() -> RuntimeDB:
    db_path = os.getenv("RUNTIME_DB_PATH")
    if not db_path:
        db_path = str(runtime_state_root() / "runtime.db")
    return RuntimeDB(db_path)


DB = _db()

# --- Panel sync scheduling (best-effort; GitHub Projects is view-layer) ---
_PANEL_DIRTY: set[str] = set()
_PANEL_LOCK = threading.Lock()
_REPO_IMPROVEMENT_STALE_AGENT_ROLES = frozenset(
    {
        crewai_self_upgrade.ROLE_PRODUCT_MANAGER,
        crewai_self_upgrade.ROLE_TEST_MANAGER,
        crewai_self_upgrade.ROLE_TEST_CASE_GAP_AGENT,
        crewai_self_upgrade.ROLE_ISSUE_DRAFTER,
        crewai_self_upgrade.ROLE_PLAN_REVIEW_AGENT,
        crewai_self_upgrade.ROLE_PLAN_QA_AGENT,
        crewai_self_upgrade.ROLE_MILESTONE_MANAGER,
        crewai_self_upgrade.ROLE_PROCESS_OPTIMIZATION_ANALYST,
        crewai_self_upgrade.ROLE_CODE_QUALITY_ANALYST,
        "Scheduler-Agent",
        "Release-Agent",
        "Process-Metrics-Agent",
    }
)


class _ScopedRunLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._active: set[str] = set()

    def acquire(self, key: str) -> bool:
        normalized = str(key or "").strip() or "__default__"
        with self._guard:
            if normalized in self._active:
                return False
            self._active.add(normalized)
            return True

    def release(self, key: str) -> None:
        normalized = str(key or "").strip() or "__default__"
        with self._guard:
            self._active.discard(normalized)


_REPO_IMPROVEMENT_LOCKS = _ScopedRunLocks()
_REPO_IMPROVEMENT_DELIVERY_LOCKS = _ScopedRunLocks()


def _is_teamos(project_id: str) -> bool:
    return str(project_id or "").strip() == "teamos"


def _workspace_root() -> Path:
    return workspace_store.workspace_root()


def _workspace_exists() -> bool:
    try:
        return _workspace_root().exists()
    except Exception:
        return False


def _list_workspace_projects() -> list[str]:
    if not _workspace_exists():
        return []
    try:
        return workspace_store.list_projects()
    except Exception:
        return []


def _all_project_ids() -> list[str]:
    # Always include teamos (repo scope), plus any workspace projects.
    ids = ["teamos"]
    for pid in _list_workspace_projects():
        if pid != "teamos":
            ids.append(pid)
    return ids


def _ensure_workspace_safe_for_project_writes() -> None:
    # Hard gate: any project truth-source artifacts must live OUTSIDE the team-os git repo.
    workspace_store.assert_project_paths_outside_repo(team_os_root=team_os_root())


def _requirements_dir(project_id: str, *, ensure: bool) -> Path:
    if _is_teamos(project_id):
        return teamos_requirements_dir()
    _ensure_workspace_safe_for_project_writes()
    if ensure:
        workspace_store.ensure_project_scaffold(project_id)
    return workspace_store.requirements_dir(project_id)


def _parse_scope_to_project_id(scope: str) -> str:
    s = str(scope or "").strip()
    if not s:
        raise HTTPException(status_code=400, detail={"error": "invalid_scope", "hint": "scope is required: teamos | project:<id>"})
    if s == "teamos":
        return "teamos"
    if s.startswith("project:"):
        pid = s.split(":", 1)[1].strip()
        if not pid:
            raise HTTPException(status_code=400, detail={"error": "invalid_scope", "hint": "scope=project:<id> missing <id>"})
        return pid
    # Backward compatible: treat a bare id as project:<id>.
    return s


def _scope_from_project_id(project_id: str) -> str:
    pid = str(project_id or "").strip()
    return "teamos" if pid == "teamos" else f"project:{pid}"


def _local_base_url() -> str:
    return str(os.getenv("TEAMOS_BASE_URL") or os.getenv("CONTROL_PLANE_BASE_URL") or "http://127.0.0.1:8787").strip()


def _publish_redis_event(*, event_type: str, actor: str, project_id: str, workstream_id: str, payload: dict[str, Any]) -> None:
    # Best-effort pubsub: never block or fail control-plane writes.
    try:
        redis_bus.publish_event(
            channel="",
            payload={
                "event_type": event_type,
                "actor": actor,
                "project_id": project_id,
                "workstream_id": workstream_id,
                "payload": payload,
                "ts": _utc_now_iso(),
            },
        )
    except Exception:
        pass


def _require_leader_write() -> dict[str, Any]:
    """
    Enforce leader-only writes (Brain-only).
    If cluster is enabled and another leader holds the lease, return HTTP 409 with leader info.
    """
    instance_id = ensure_instance_id()
    cfg = cluster_manager.load_cluster_config()
    if not cluster_manager.cluster_enabled(cfg):
        return {"leader_instance_id": instance_id, "leader_base_url": _local_base_url(), "backend": "local", "lease_expires_at": ""}

    cur = cluster_manager.read_leader(cfg)
    if cur and cur.leader_instance_id and (cur.leader_instance_id != instance_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "NOT_LEADER",
                "instance_id": instance_id,
                "leader_instance_id": cur.leader_instance_id,
                "leader_base_url": cur.leader_base_url,
                "lease_expires_at": cur.lease_expires_at,
                "backend": cur.backend,
                "issue_url": cur.issue_url,
            },
        )
    return cur.__dict__ if cur else {"leader_instance_id": instance_id, "leader_base_url": _local_base_url(), "backend": "local", "lease_expires_at": ""}


def _leader_write_allowed() -> bool:
    try:
        _require_leader_write()
        return True
    except HTTPException as exc:
        if int(getattr(exc, "status_code", 500)) == 409:
            return False
        raise


def _iso_age_seconds(ts: Any) -> float:
    raw = str(ts or "").strip()
    if not raw:
        return 0.0
    try:
        import datetime as _dt

        dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return max(0.0, (_dt.datetime.now(_dt.timezone.utc) - dt).total_seconds())
    except Exception:
        return 0.0


def _repo_improvement_env(name: str, default: str = "", *, legacy_name: str = "") -> str:
    raw = os.getenv(name)
    if raw is not None:
        return raw
    legacy = legacy_name.strip() or name.replace("TEAMOS_REPO_IMPROVEMENT_", "TEAMOS_SELF_UPGRADE_")
    if legacy and legacy != name:
        legacy_raw = os.getenv(legacy)
        if legacy_raw is not None:
            return legacy_raw
    return default


def _env_truthy(name: str, default: str = "1") -> bool:
    v = _repo_improvement_env(name, default).strip().lower()
    return v not in ("0", "false", "no", "off", "")


def _repo_improvement_flow(value: Any) -> bool:
    flow = str(value or "").strip().lower()
    return flow in ("repo_improvement", "self_upgrade")


def _repo_improvement_run_id_set(*, event_limit: int = 5000) -> set[str]:
    run_ids: set[str] = set()
    try:
        events = DB.list_events(limit=max(1, int(event_limit)))
    except Exception:
        return run_ids
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            continue
        event_type = str(event.event_type or "").strip().upper()
        if event_type.startswith("REPO_IMPROVEMENT_"):
            run_ids.add(run_id)
            continue
        if event_type in {"RUN_STARTED", "RUN_FAILED", "RUN_FINISHED"} and _repo_improvement_flow(payload.get("flow")):
            run_ids.add(run_id)
    return run_ids


def _is_repo_improvement_run(run: Any, *, known_run_ids: set[str]) -> bool:
    run_id = str(getattr(run, "run_id", "") or "").strip()
    if run_id and run_id in known_run_ids:
        return True
    objective = str(getattr(run, "objective", "") or "").strip().lower()
    run_id_lower = run_id.lower()
    return (
        "repo-improvement" in objective
        or "repo-improvement" in run_id_lower
        or "self-upgrade" in objective
        or "self-upgrade" in run_id_lower
    )


def _cleanup_stale_repo_improvement_activity() -> None:
    ttl_sec = max(300, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_STALE_TTL_SEC", "900") or "900"))
    known_run_ids = _repo_improvement_run_id_set()

    for run in DB.list_runs():
        if _normalize_run_state(run.state) != "RUNNING":
            continue
        if not _is_repo_improvement_run(run, known_run_ids=known_run_ids):
            continue
        if _iso_age_seconds(run.last_update) < float(ttl_sec):
            continue
        DB.update_run_state(run_id=str(run.run_id), state="FAILED")
        try:
            DB.add_event(
                event_type="REPO_IMPROVEMENT_STALE_RUN_CLEANED",
                actor="control-plane.cleanup",
                project_id=str(run.project_id or "teamos"),
                workstream_id=str(run.workstream_id or _default_workstream_id()),
                payload={"run_id": str(run.run_id or ""), "objective": str(run.objective or ""), "ttl_sec": ttl_sec},
            )
        except Exception:
            pass

    for agent in DB.list_agents():
        if str(agent.state or "").strip().upper() != "RUNNING":
            continue
        role_id = str(agent.role_id or "").strip()
        current_action = str(agent.current_action or "").strip().lower()
        if role_id not in _REPO_IMPROVEMENT_STALE_AGENT_ROLES and "repo-improvement" not in current_action and "self-upgrade" not in current_action:
            continue
        if _iso_age_seconds(agent.last_heartbeat) < float(ttl_sec):
            continue
        try:
            DB.update_assignment(
                agent_id=str(agent.agent_id),
                state="FAILED",
                current_action="stale repo-improvement activity cleaned on startup",
            )
        except Exception:
            pass
        try:
            DB.add_event(
                event_type="REPO_IMPROVEMENT_STALE_AGENT_CLEANED",
                actor="control-plane.cleanup",
                project_id=str(agent.project_id or "teamos"),
                workstream_id=str(agent.workstream_id or _default_workstream_id()),
                payload={"agent_id": str(agent.agent_id or ""), "role_id": role_id, "ttl_sec": ttl_sec},
            )
        except Exception:
            pass


def _plan_dir(project_id: str, *, ensure: bool) -> Path:
    if _is_teamos(project_id):
        # teamos plan stays in-repo (scope=teamos).
        return team_os_root() / "docs" / "plans" / "teamos"
    _ensure_workspace_safe_for_project_writes()
    if ensure:
        workspace_store.ensure_project_scaffold(project_id)
    return workspace_store.plan_dir(project_id)


def _ledger_tasks_dir(project_id: str, *, ensure: bool) -> Path:
    if _is_teamos(project_id):
        return ledger_tasks_dir()
    _ensure_workspace_safe_for_project_writes()
    if ensure:
        workspace_store.ensure_project_scaffold(project_id)
    return workspace_store.ledger_tasks_dir(project_id)


def _logs_tasks_dir(project_id: str, *, ensure: bool) -> Path:
    if _is_teamos(project_id):
        return logs_tasks_dir()
    _ensure_workspace_safe_for_project_writes()
    if ensure:
        workspace_store.ensure_project_scaffold(project_id)
    return workspace_store.logs_tasks_dir(project_id)


def _conversations_dir(project_id: str, *, ensure: bool) -> Path:
    if _is_teamos(project_id):
        return runtime_state_root() / "ledger" / "conversations" / "teamos"
    _ensure_workspace_safe_for_project_writes()
    if ensure:
        workspace_store.ensure_project_scaffold(project_id)
    return workspace_store.conversations_dir(project_id)


def _active_projects_summary() -> list[dict[str, Any]]:
    # Projects are discovered from workspace. Team OS self is always present.
    ws_list = _list_workspace_projects()
    out: list[dict[str, Any]] = []
    # Team OS self project (repo scope)
    out.append({"project_id": "teamos", "name": "Team OS Development", "workstreams": [w.get("id") for w in (load_workstreams() or []) if w.get("id")]})
    for pid in ws_list:
        if pid == "teamos":
            continue
        out.append({"project_id": pid, "name": pid, "workstreams": []})
    return out


def _panel_github_writes_enabled() -> bool:
    # Extra safety gate: explicit opt-in for remote writes.
    return _env_truthy("TEAMOS_PANEL_GH_WRITE_ENABLED", "0")


def _mark_panel_dirty(project_id: Optional[str] = None) -> None:
    with _PANEL_LOCK:
        if project_id:
            _PANEL_DIRTY.add(str(project_id))
        else:
            for pid in _all_project_ids():
                _PANEL_DIRTY.add(pid)


def _target_repo_configured(target: dict[str, Any]) -> bool:
    repo_url = str(target.get("repo_url") or "").strip()
    if repo_url:
        return True
    repo_root = str(target.get("repo_root") or "").strip()
    if not repo_root:
        return False
    marker = Path(repo_root).expanduser() / ".git"
    return marker.is_dir() or marker.is_file()


def _effective_repo_improvement_project_id(*, project_id: str, target_id: str = "") -> str:
    normalized = str(project_id or "teamos").strip() or "teamos"
    tid = str(target_id or "").strip()
    if not tid:
        return normalized
    target = improvement_store.get_target(tid) or {}
    target_project_id = str(target.get("project_id") or "").strip()
    return target_project_id or normalized


def _default_local_improvement_target() -> Optional[dict[str, Any]]:
    repo_root = Path(os.getenv("TEAM_OS_REPO_PATH") or str(team_os_root())).expanduser().resolve()
    marker = repo_root / ".git"
    if not repo_root.exists() or not (marker.is_dir() or marker.is_file()):
        return None
    return improvement_store.ensure_target(project_id="teamos", repo_path=str(repo_root))


def _enabled_improvement_targets(*, auto_mode: str) -> list[dict[str, Any]]:
    targets = improvement_store.list_targets(enabled_only=True)
    selected: list[dict[str, Any]] = []
    flag_name = "auto_delivery" if auto_mode == "delivery" else "auto_discovery"
    for target in targets:
        if not bool(target.get(flag_name)):
            continue
        if not _target_repo_configured(target):
            continue
        selected.append(target)
    if not targets:
        default_target = _default_local_improvement_target()
        return [default_target] if default_target else []
    return selected


def _repo_improvement_lock_key(
    *,
    project_id: str,
    target_id: str = "",
    repo_path: str = "",
    repo_url: str = "",
    repo_locator: str = "",
) -> str:
    normalized_target_id = str(target_id or "").strip()
    if normalized_target_id:
        return f"target:{normalized_target_id}"
    normalized_repo_path = str(repo_path or "").strip()
    if normalized_repo_path:
        return f"repo_path:{normalized_repo_path}"
    normalized_repo_url = str(repo_url or "").strip()
    if normalized_repo_url:
        return f"repo_url:{normalized_repo_url}"
    normalized_repo_locator = str(repo_locator or "").strip()
    if normalized_repo_locator:
        return f"repo_locator:{normalized_repo_locator}"
    return f"project:{str(project_id or 'teamos').strip() or 'teamos'}"


def _repo_improvement_delivery_lock_key(
    *,
    project_id: str,
    target_id: str = "",
    task_id: str = "",
) -> str:
    normalized_task_id = str(task_id or "").strip()
    if normalized_task_id:
        return f"task:{normalized_task_id}"
    normalized_target_id = str(target_id or "").strip()
    if normalized_target_id:
        return f"target:{normalized_target_id}"
    return f"project:{str(project_id or 'teamos').strip() or 'teamos'}"


def _run_self_upgrade_iteration(
    *,
    actor: str,
    project_id: str = "teamos",
    workstream_id: str = "general",
    objective: str = "",
    target_id: str = "",
    repo_path: str = "",
    repo_url: str = "",
    repo_locator: str = "",
    dry_run: bool = False,
    force: bool = False,
    trigger: str = "api",
) -> dict[str, Any]:
    effective_project_id = _effective_repo_improvement_project_id(project_id=project_id, target_id=target_id)
    _cleanup_stale_repo_improvement_activity()
    lock_key = _repo_improvement_lock_key(
        project_id=effective_project_id,
        target_id=target_id,
        repo_path=repo_path,
        repo_url=repo_url,
        repo_locator=repo_locator,
    )
    if not _REPO_IMPROVEMENT_LOCKS.acquire(lock_key):
        payload = {
            "ok": True,
            "skipped": True,
            "reason": "repo_improvement_already_running",
            "project_id": effective_project_id,
            "workstream_id": workstream_id,
            "trigger": trigger,
            "target_id": str(target_id or "").strip(),
            "lock_key": lock_key,
        }
        try:
            DB.add_event(
                event_type="REPO_IMPROVEMENT_ALREADY_RUNNING",
                actor=actor,
                project_id=effective_project_id,
                workstream_id=workstream_id,
                payload=payload,
            )
        except Exception:
            pass
        return payload

    try:
        spec = crewai_orchestrator.RunSpec(
            project_id=effective_project_id,
            workstream_id=workstream_id,
            objective=objective or "Run CrewAI repo-improvement for the target repository",
            flow="repo_improvement",
            target_id=target_id,
            repo_path=repo_path,
            repo_url=repo_url,
            repo_locator=repo_locator,
            dry_run=dry_run,
            force=force,
            trigger=trigger,
        )
        out = crewai_orchestrator.run_once(db=DB, spec=spec, actor=actor)
        _mark_panel_dirty(project_id)
        return out
    finally:
        _REPO_IMPROVEMENT_LOCKS.release(lock_key)


def _run_self_upgrade_discussion_sync(*, actor: str) -> dict[str, Any]:
    out = crewai_self_upgrade.reconcile_feature_discussions(
        db=DB,
        actor=actor,
        verbose=_env_truthy("TEAMOS_REPO_IMPROVEMENT_VERBOSE", "0"),
    )
    _mark_panel_dirty("teamos")
    return out


def _run_self_upgrade_delivery_iteration(
    *,
    actor: str,
    project_id: str = "teamos",
    target_id: str = "",
    task_id: str = "",
    dry_run: bool = False,
    force: bool = False,
    max_tasks: Optional[int] = None,
) -> dict[str, Any]:
    _cleanup_stale_repo_improvement_activity()
    current_project_id = _effective_repo_improvement_project_id(
        project_id=str(project_id or "teamos").strip() or "teamos",
        target_id=target_id,
    )
    lock_key = _repo_improvement_delivery_lock_key(
        project_id=current_project_id,
        target_id=target_id,
        task_id=task_id,
    )
    if not _REPO_IMPROVEMENT_DELIVERY_LOCKS.acquire(lock_key):
        payload = {
            "ok": True,
            "skipped": True,
            "reason": "repo_improvement_delivery_already_running",
            "project_id": current_project_id,
            "task_id": str(task_id or "").strip(),
            "target_id": str(target_id or "").strip(),
            "lock_key": lock_key,
        }
        try:
            DB.add_event(
                event_type="REPO_IMPROVEMENT_DELIVERY_ALREADY_RUNNING",
                actor=actor,
                project_id=current_project_id,
                workstream_id=_default_workstream_id(),
                payload=payload,
            )
        except Exception:
            pass
        return payload

    run_key = str(task_id or current_project_id or "teamos").strip() or "teamos"
    run_id = f"run-{run_key}::repo-improvement-delivery"
    objective = (
        f"Resume repo-improvement delivery for task {task_id}"
        if str(task_id or "").strip()
        else f"Run repo-improvement delivery sweep for project {current_project_id}"
    )
    DB.upsert_run(run_id=run_id, project_id=current_project_id, workstream_id=_default_workstream_id(), objective=objective, state="RUNNING")
    try:
        out = crewai_self_upgrade_delivery.run_delivery_sweep(
            db=DB,
            actor=actor,
            project_id=current_project_id,
            target_id=str(target_id or "").strip(),
            task_id=str(task_id or "").strip(),
            dry_run=bool(dry_run),
            force=bool(force),
            max_tasks=max_tasks,
        )
        run_state = "DONE" if bool(out.get("ok")) else "FAILED"
        DB.upsert_run(run_id=run_id, project_id=current_project_id, workstream_id=_default_workstream_id(), objective=objective, state=run_state)
        _mark_panel_dirty(current_project_id)
        return out
    except Exception as exc:
        DB.upsert_run(run_id=run_id, project_id=current_project_id, workstream_id=_default_workstream_id(), objective=objective, state="FAILED")
        try:
            DB.add_event(
                event_type="REPO_IMPROVEMENT_DELIVERY_SWEEP_FAILED",
                actor=actor,
                project_id=current_project_id,
                workstream_id=_default_workstream_id(),
                payload={"task_id": str(task_id or ""), "error": str(exc)[:300]},
            )
        except Exception:
            pass
        raise
    finally:
        _REPO_IMPROVEMENT_DELIVERY_LOCKS.release(lock_key)


def _panel_auto_sync_loop() -> None:
    # Auto sync is best-effort; missing config/auth will simply skip.
    interval_sec = int(os.getenv("TEAMOS_PANEL_GH_SYNC_INTERVAL_SEC", "60") or "60")
    debounce_sec = int(os.getenv("TEAMOS_PANEL_GH_SYNC_DEBOUNCE_SEC", "30") or "30")
    last_attempt: dict[str, float] = {}

    while True:
        try:
            # Safety: default off. Enabling auto-sync implies periodic remote writes to GitHub Projects (view-layer).
            if not _env_truthy("TEAMOS_PANEL_GH_AUTO_SYNC", "0"):
                time.sleep(5)
                continue
            if not _leader_write_allowed():
                time.sleep(5)
                continue
            if not _panel_github_writes_enabled():
                time.sleep(5)
                continue

            try:
                mapping = load_mapping()
            except PanelMappingError:
                time.sleep(interval_sec)
                continue

            projects = (mapping.data.get("projects") or {}) if isinstance(mapping.data.get("projects"), dict) else {}
            dirty: set[str] = set()
            with _PANEL_LOCK:
                dirty = set(_PANEL_DIRTY)
                _PANEL_DIRTY.clear()

            for pid, cfg in projects.items():
                if not isinstance(cfg, dict):
                    continue
                # Skip if not bound to a real GitHub project.
                owner = str(cfg.get("owner") or "").strip()
                pnum = int(cfg.get("project_number") or 0)
                pnode = str(cfg.get("project_node_id") or "").strip()
                if not pnode and (not owner or pnum <= 0):
                    continue

                now = time.time()
                if pid not in dirty:
                    # Periodic refresh, but debounced.
                    if (now - last_attempt.get(pid, 0.0)) < float(interval_sec):
                        continue

                if (now - last_attempt.get(pid, 0.0)) < float(debounce_sec):
                    continue

                last_attempt[pid] = now
                svc = GitHubProjectsPanelSync(db=DB)
                ts_start = _utc_now_iso()
                ok = True
                err = ""
                res: dict[str, Any] = {}
                try:
                    res = svc.sync(project_id=str(pid), mode="incremental", dry_run=False)
                    DB.add_event(
                        event_type="PANEL_SYNC_AUTO_OK",
                        actor="control-plane",
                        project_id=str(pid),
                        workstream_id=_default_workstream_id(),
                        payload={"panel": "github_projects", "stats": res.get("stats") or {}},
                    )
                except Exception as e:
                    ok = False
                    err = str(e)
                    DB.add_event(
                        event_type="PANEL_SYNC_AUTO_FAIL",
                        actor="control-plane",
                        project_id=str(pid),
                        workstream_id=_default_workstream_id(),
                        payload={"panel": "github_projects", "error": err[:500]},
                    )
                finally:
                    try:
                        stats = (res.get("stats") or {}) if isinstance(res, dict) else {}
                        DB.record_panel_sync_run(
                            project_id=str(pid),
                            panel_type="github_projects",
                            mode="incremental",
                            dry_run=False,
                            ok=ok,
                            stats=stats if isinstance(stats, dict) else {"_raw": str(stats)},
                            error=err,
                            ts_start=ts_start,
                            ts_end=_utc_now_iso(),
                        )
                    except Exception:
                        pass

        except Exception:
            # Never crash the server because of panel sync.
            pass

        time.sleep(5)


@app.on_event("startup")
def _startup_background_threads() -> None:
    try:
        _cleanup_stale_repo_improvement_activity()
    except Exception:
        pass

    def _repo_improvement_cleanup_loop() -> None:
        interval_sec = max(30, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_CLEANUP_INTERVAL_SEC", "60") or "60"))
        _set_repo_improvement_loop_state(
            _REPO_IMPROVEMENT_LOOP_CLEANUP,
            enabled=True,
            status="starting",
            current_action="initializing cleanup loop",
            interval_sec=interval_sec,
        )
        try:
            while True:
                try:
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_CLEANUP,
                        enabled=True,
                        status="running",
                        current_action="cleaning stale repo-improvement activity",
                        interval_sec=interval_sec,
                        last_started_at=_utc_now_iso(),
                    )
                    _cleanup_stale_repo_improvement_activity()
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_CLEANUP,
                        enabled=True,
                        status="sleeping",
                        current_action=f"sleeping {interval_sec}s until next cleanup sweep",
                        interval_sec=interval_sec,
                        last_completed_at=_utc_now_iso(),
                    )
                except Exception:
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_CLEANUP,
                        enabled=True,
                        status="error",
                        current_action="cleanup loop raised an exception",
                        interval_sec=interval_sec,
                    )
                time.sleep(interval_sec)
        except Exception:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_CLEANUP,
                enabled=True,
                status="stopped",
                current_action="cleanup loop stopped",
                interval_sec=interval_sec,
            )

    rct = threading.Thread(target=_repo_improvement_cleanup_loop, name="repo-improvement-cleanup-loop", daemon=True)
    rct.start()

    def _self_upgrade_worktree_migration_once() -> None:
        try:
            time.sleep(1)
            if not _leader_write_allowed():
                return
            out = crewai_self_upgrade_delivery.migrate_legacy_worktrees(project_id="teamos")
            if int(out.get("updated") or 0) > 0 or int(out.get("moved") or 0) > 0:
                DB.add_event(
                    event_type="REPO_IMPROVEMENT_WORKTREE_MIGRATED",
                    actor="control-plane.startup",
                    project_id="teamos",
                    workstream_id=_default_workstream_id(),
                    payload={"updated": int(out.get("updated") or 0), "moved": int(out.get("moved") or 0)},
                )
        except Exception as e:
            try:
                DB.add_event(
                    event_type="REPO_IMPROVEMENT_WORKTREE_MIGRATION_FAILED",
                    actor="control-plane.startup",
                    project_id="teamos",
                    workstream_id=_default_workstream_id(),
                    payload={"error": str(e)[:300]},
                )
            except Exception:
                pass

    wmt = threading.Thread(target=_self_upgrade_worktree_migration_once, name="self-upgrade-worktree-migration-once", daemon=True)
    wmt.start()

    # GitHub Projects sync loop (view layer). It is a best-effort background thread.
    t = threading.Thread(target=_panel_auto_sync_loop, name="panel-auto-sync", daemon=True)
    t.start()

    # Recovery auto-run: on startup, scan unfinished tasks and attempt resume.
    def _recovery_auto_once() -> None:
        # Never crash the server because of recovery.
        if str(os.getenv("TEAMOS_RECOVERY_AUTO", "1") or "").strip().lower() in ("0", "false", "no", "off"):
            return
        try:
            time.sleep(2)
            _ = v1_recovery_scan()
            _ = v1_recovery_resume(RecoveryResumeIn(all=True))
        except Exception:
            pass

    rt = threading.Thread(target=_recovery_auto_once, name="recovery-auto-once", daemon=True)
    rt.start()

    def _self_upgrade_auto_once() -> None:
        if not _env_truthy("TEAMOS_REPO_IMPROVEMENT_AUTO", "1"):
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_STARTUP,
                enabled=False,
                status="disabled",
                current_action="startup repo-improvement sweep disabled",
            )
            return
        try:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_STARTUP,
                enabled=True,
                status="waiting",
                current_action="waiting 4s before startup repo-improvement sweep",
                initial_delay_sec=4,
            )
            time.sleep(4)
            if not _leader_write_allowed():
                _set_repo_improvement_loop_state(
                    _REPO_IMPROVEMENT_LOOP_STARTUP,
                    enabled=True,
                    status="skipped",
                    current_action="startup repo-improvement sweep skipped because this node is not leader",
                )
                return
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_STARTUP,
                enabled=True,
                status="running",
                current_action="running startup repo-improvement sweeps",
                last_started_at=_utc_now_iso(),
            )
            for target in _enabled_improvement_targets(auto_mode="discovery"):
                _ = _run_self_upgrade_iteration(
                    actor="control-plane.startup",
                    project_id=str(target.get("project_id") or "teamos").strip() or "teamos",
                    workstream_id=str(target.get("workstream_id") or "general").strip() or "general",
                    objective=f"Startup improvement sweep for target {str(target.get('display_name') or target.get('target_id') or 'target')}",
                    target_id=str(target.get("target_id") or "").strip(),
                    repo_path=str(target.get("repo_root") or "").strip(),
                    repo_locator=str(target.get("repo_locator") or "").strip(),
                    dry_run=False,
                    force=False,
                    trigger="startup_auto",
                )
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_STARTUP,
                enabled=True,
                status="done",
                current_action="startup repo-improvement sweeps completed",
                last_completed_at=_utc_now_iso(),
            )
        except Exception as e:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_STARTUP,
                enabled=True,
                status="error",
                current_action=f"startup repo-improvement sweep failed: {str(e)[:160]}",
                last_error=str(e)[:300],
            )
            try:
                DB.add_event(
                    event_type="REPO_IMPROVEMENT_AUTO_FAILED",
                    actor="control-plane.startup",
                    project_id="teamos",
                    workstream_id=_default_workstream_id(),
                    payload={"error": str(e)[:300]},
                )
            except Exception:
                pass

    sut = threading.Thread(target=_self_upgrade_auto_once, name="self-upgrade-auto-once", daemon=True)
    sut.start()

    def _self_upgrade_continuous_loop() -> None:
        if not _env_truthy("TEAMOS_REPO_IMPROVEMENT_CONTINUOUS", "1"):
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DISCOVERY,
                enabled=False,
                status="disabled",
                current_action="continuous repo-improvement discovery disabled",
            )
            return
        interval_sec = max(0, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_LOOP_INTERVAL_SEC", "300") or "300"))
        initial_delay_sec = max(0, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_LOOP_INITIAL_DELAY_SEC", "90") or "90"))
        retry_sleep_sec = max(1, interval_sec)
        try:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DISCOVERY,
                enabled=True,
                status="waiting",
                current_action=f"waiting {initial_delay_sec}s before first discovery sweep",
                interval_sec=interval_sec,
                initial_delay_sec=initial_delay_sec,
            )
            time.sleep(initial_delay_sec)
            while True:
                try:
                    if not _leader_write_allowed():
                        _set_repo_improvement_loop_state(
                            _REPO_IMPROVEMENT_LOOP_DISCOVERY,
                            enabled=True,
                            status="sleeping",
                            current_action=(
                                f"discovery loop waiting {retry_sleep_sec}s because this node is not leader"
                            ),
                            interval_sec=interval_sec,
                        )
                        time.sleep(retry_sleep_sec)
                        continue
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DISCOVERY,
                        enabled=True,
                        status="running",
                        current_action="running continuous repo-improvement discovery sweeps",
                        interval_sec=interval_sec,
                        last_started_at=_utc_now_iso(),
                    )
                    for target in _enabled_improvement_targets(auto_mode="discovery"):
                        _ = _run_self_upgrade_iteration(
                            actor="control-plane.loop",
                            project_id=str(target.get("project_id") or "teamos").strip() or "teamos",
                            workstream_id=str(target.get("workstream_id") or "general").strip() or "general",
                            objective=f"Continuous improvement sweep for target {str(target.get('display_name') or target.get('target_id') or 'target')}",
                            target_id=str(target.get("target_id") or "").strip(),
                            repo_path=str(target.get("repo_root") or "").strip(),
                            repo_locator=str(target.get("repo_locator") or "").strip(),
                            dry_run=False,
                            force=False,
                            trigger="continuous_loop",
                        )
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DISCOVERY,
                        enabled=True,
                        status="sleeping",
                        current_action=(
                            "immediately scheduling next discovery sweep"
                            if interval_sec <= 0
                            else f"sleeping {interval_sec}s until next discovery sweep"
                        ),
                        interval_sec=interval_sec,
                        last_completed_at=_utc_now_iso(),
                    )
                except Exception as e:
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DISCOVERY,
                        enabled=True,
                        status="error",
                        current_action=f"discovery loop failed: {str(e)[:160]}",
                        interval_sec=interval_sec,
                        last_error=str(e)[:300],
                    )
                    try:
                        DB.add_event(
                            event_type="REPO_IMPROVEMENT_LOOP_FAILED",
                            actor="control-plane.loop",
                            project_id="teamos",
                            workstream_id=_default_workstream_id(),
                            payload={"error": str(e)[:300]},
                        )
                    except Exception:
                        pass
                time.sleep(interval_sec)
        except Exception:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DISCOVERY,
                enabled=True,
                status="stopped",
                current_action="discovery loop stopped",
                interval_sec=interval_sec,
            )

    clt = threading.Thread(target=_self_upgrade_continuous_loop, name="self-upgrade-continuous-loop", daemon=True)
    clt.start()

    def _self_upgrade_discussion_loop() -> None:
        if not _env_truthy("TEAMOS_REPO_IMPROVEMENT_DISCUSSION_AUTO", "1"):
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DISCUSSION,
                enabled=False,
                status="disabled",
                current_action="repo-improvement discussion loop disabled",
            )
            return
        interval_sec = max(30, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_DISCUSSION_INTERVAL_SEC", "90") or "90"))
        initial_delay_sec = max(10, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_DISCUSSION_INITIAL_DELAY_SEC", "30") or "30"))
        try:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DISCUSSION,
                enabled=True,
                status="waiting",
                current_action=f"waiting {initial_delay_sec}s before first discussion sync",
                interval_sec=interval_sec,
                initial_delay_sec=initial_delay_sec,
            )
            time.sleep(initial_delay_sec)
            while True:
                try:
                    if not _leader_write_allowed():
                        _set_repo_improvement_loop_state(
                            _REPO_IMPROVEMENT_LOOP_DISCUSSION,
                            enabled=True,
                            status="sleeping",
                            current_action=f"discussion loop waiting {interval_sec}s because this node is not leader",
                            interval_sec=interval_sec,
                        )
                        time.sleep(interval_sec)
                        continue
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DISCUSSION,
                        enabled=True,
                        status="running",
                        current_action="running repo-improvement discussion sync",
                        interval_sec=interval_sec,
                        last_started_at=_utc_now_iso(),
                    )
                    _ = _run_self_upgrade_discussion_sync(actor="control-plane.discussion-loop")
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DISCUSSION,
                        enabled=True,
                        status="sleeping",
                        current_action=f"sleeping {interval_sec}s until next discussion sync",
                        interval_sec=interval_sec,
                        last_completed_at=_utc_now_iso(),
                    )
                except Exception as e:
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DISCUSSION,
                        enabled=True,
                        status="error",
                        current_action=f"discussion loop failed: {str(e)[:160]}",
                        interval_sec=interval_sec,
                        last_error=str(e)[:300],
                    )
                    try:
                        DB.add_event(
                            event_type="REPO_IMPROVEMENT_DISCUSSION_LOOP_FAILED",
                            actor="control-plane.discussion-loop",
                            project_id="teamos",
                            workstream_id=_default_workstream_id(),
                            payload={"error": str(e)[:300]},
                        )
                    except Exception:
                        pass
                time.sleep(interval_sec)
        except Exception:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DISCUSSION,
                enabled=True,
                status="stopped",
                current_action="discussion loop stopped",
                interval_sec=interval_sec,
            )

    dlt = threading.Thread(target=_self_upgrade_discussion_loop, name="self-upgrade-discussion-loop", daemon=True)
    dlt.start()

    def _self_upgrade_delivery_loop() -> None:
        if not _env_truthy("TEAMOS_REPO_IMPROVEMENT_DELIVERY_AUTO", "1"):
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DELIVERY,
                enabled=False,
                status="disabled",
                current_action="repo-improvement delivery loop disabled",
            )
            return
        interval_sec = max(30, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_DELIVERY_INTERVAL_SEC", "180") or "180"))
        initial_delay_sec = max(10, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_DELIVERY_INITIAL_DELAY_SEC", "45") or "45"))
        max_tasks = max(1, int(_repo_improvement_env("TEAMOS_REPO_IMPROVEMENT_DELIVERY_MAX_TASKS_PER_SWEEP", "1") or "1"))
        try:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DELIVERY,
                enabled=True,
                status="waiting",
                current_action=f"waiting {initial_delay_sec}s before first delivery sweep",
                interval_sec=interval_sec,
                initial_delay_sec=initial_delay_sec,
                max_tasks=max_tasks,
            )
            time.sleep(initial_delay_sec)
            while True:
                try:
                    if not _leader_write_allowed():
                        _set_repo_improvement_loop_state(
                            _REPO_IMPROVEMENT_LOOP_DELIVERY,
                            enabled=True,
                            status="sleeping",
                            current_action=f"delivery loop waiting {interval_sec}s because this node is not leader",
                            interval_sec=interval_sec,
                            max_tasks=max_tasks,
                        )
                        time.sleep(interval_sec)
                        continue
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DELIVERY,
                        enabled=True,
                        status="running",
                        current_action=f"running delivery sweeps (max_tasks={max_tasks})",
                        interval_sec=interval_sec,
                        max_tasks=max_tasks,
                        last_started_at=_utc_now_iso(),
                    )
                    for target in _enabled_improvement_targets(auto_mode="delivery"):
                        _ = _run_self_upgrade_delivery_iteration(
                            actor="control-plane.delivery-loop",
                            project_id=str(target.get("project_id") or "teamos").strip() or "teamos",
                            target_id=str(target.get("target_id") or "").strip(),
                            dry_run=False,
                            force=False,
                            max_tasks=max_tasks,
                        )
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DELIVERY,
                        enabled=True,
                        status="sleeping",
                        current_action=f"sleeping {interval_sec}s until next delivery sweep",
                        interval_sec=interval_sec,
                        max_tasks=max_tasks,
                        last_completed_at=_utc_now_iso(),
                    )
                except Exception as e:
                    _set_repo_improvement_loop_state(
                        _REPO_IMPROVEMENT_LOOP_DELIVERY,
                        enabled=True,
                        status="error",
                        current_action=f"delivery loop failed: {str(e)[:160]}",
                        interval_sec=interval_sec,
                        max_tasks=max_tasks,
                        last_error=str(e)[:300],
                    )
                    try:
                        DB.add_event(
                            event_type="REPO_IMPROVEMENT_DELIVERY_LOOP_FAILED",
                            actor="control-plane.delivery-loop",
                            project_id="teamos",
                            workstream_id=_default_workstream_id(),
                            payload={"error": str(e)[:300]},
                        )
                    except Exception:
                        pass
                time.sleep(interval_sec)
        except Exception:
            _set_repo_improvement_loop_state(
                _REPO_IMPROVEMENT_LOOP_DELIVERY,
                enabled=True,
                status="stopped",
                current_action="delivery loop stopped",
                interval_sec=interval_sec,
                max_tasks=max_tasks,
            )

    sdt = threading.Thread(target=_self_upgrade_delivery_loop, name="self-upgrade-delivery-loop", daemon=True)
    sdt.start()

    def _openclaw_reporting_loop() -> None:
        if not _env_truthy("TEAMOS_OPENCLAW_AUTO", "0"):
            return
        interval_sec = max(15, int(os.getenv("TEAMOS_OPENCLAW_INTERVAL_SEC", "30") or "30"))
        initial_delay_sec = max(3, int(os.getenv("TEAMOS_OPENCLAW_INITIAL_DELAY_SEC", "10") or "10"))
        try:
            time.sleep(initial_delay_sec)
            while True:
                try:
                    if not _leader_write_allowed():
                        time.sleep(interval_sec)
                        continue
                    _ = openclaw_reporter.sweep_events(db=DB, dry_run=False, limit=200)
                except Exception as e:
                    try:
                        DB.add_event(
                            event_type="OPENCLAW_REPORTING_LOOP_FAILED",
                            actor="control-plane.openclaw-loop",
                            project_id="teamos",
                            workstream_id=_default_workstream_id(),
                            payload={"error": str(e)[:300]},
                        )
                    except Exception:
                        pass
                time.sleep(interval_sec)
        except Exception:
            pass

    oct = threading.Thread(target=_openclaw_reporting_loop, name="openclaw-reporting-loop", daemon=True)
    oct.start()

    # Startup indicator for optional Redis runtime bus.
    try:
        DB.add_event(
            event_type="REDIS_BUS_STATUS",
            actor="control-plane",
            project_id=_default_project_id(),
            workstream_id=_default_workstream_id(),
            payload=redis_bus.describe(),
        )
    except Exception:
        pass


def _seed_if_enabled() -> None:
    if os.getenv("TEAMOS_DEMO_SEED", "").strip() in ("1", "true", "TRUE", "yes", "YES"):
        # Seed minimal demo data for each existing workspace project (safe; idempotent per project_id).
        # This does not create any project truth-source files inside the team-os repo.
        for pid in _list_workspace_projects():
            seed_mock_data(DB, project_id=pid, workstream_id="general")


_seed_if_enabled()


class FocusUpdate(BaseModel):
    objective: str = Field(..., min_length=1)
    scope: Optional[list[str]] = None
    constraints: Optional[list[str]] = None
    success_metrics: Optional[list[str]] = None
    note: Optional[str] = None


class ChatIn(BaseModel):
    profile: Optional[str] = None
    project_id: Optional[str] = None
    workstream_id: Optional[str] = None
    run_id: Optional[str] = None
    message: str = Field(..., min_length=1)
    message_type: str = "GENERAL"  # GENERAL|NEW_REQUIREMENT|CLARIFY|DECISION|STOP|PAUSE|RESUME


class RequirementIn(BaseModel):
    project_id: str
    workstream_id: Optional[str] = None
    requirement_text: str = Field(..., min_length=1)
    priority: Optional[str] = "P2"  # P0..P3
    rationale: Optional[str] = ""
    constraints: Optional[list[str]] = None
    acceptance: Optional[list[str]] = None
    source: Optional[str] = "api"


class RequirementAddV2In(BaseModel):
    scope: str = Field(..., min_length=1, description="teamos | project:<project_id>")
    text: str = Field(..., min_length=1)
    workstream_id: Optional[str] = None
    priority: Optional[str] = "P2"  # P0..P3
    rationale: Optional[str] = ""
    constraints: Optional[list[str]] = None
    acceptance: Optional[list[str]] = None
    source: Optional[str] = "api"  # cli|api|chat|import


class RequirementImportV2In(BaseModel):
    scope: str = Field(..., min_length=1)
    filename: Optional[str] = None
    content_text: str = Field(..., min_length=1, description="File content to import (treated as a raw requirement input)")
    workstream_id: Optional[str] = None
    source: Optional[str] = "import"


class RequirementVerifyV2In(BaseModel):
    scope: str = Field(..., min_length=1)


class RequirementRebuildV2In(BaseModel):
    scope: str = Field(..., min_length=1)


class BaselineSetV2In(BaseModel):
    scope: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1, description="New baseline v2 text (verbatim)")
    reason: str = Field(..., min_length=1, description="Why baseline must be restated as v2 (requires PM decision)")


class PanelSyncIn(BaseModel):
    project_id: str
    mode: str = "incremental"  # incremental|full
    dry_run: bool = False


class SelfUpgradeIn(BaseModel):
    dry_run: bool = False
    force: bool = False
    trigger: str = "api"  # api|cli_auto|manual
    project_id: str = "teamos"
    workstream_id: str = "general"
    target_id: Optional[str] = None
    repo_path: Optional[str] = None
    repo_url: Optional[str] = None
    repo_locator: Optional[str] = None
    objective: Optional[str] = None


SelfImproveIn = SelfUpgradeIn


class SelfUpgradeDeliveryIn(BaseModel):
    dry_run: bool = False
    force: bool = False
    project_id: str = "teamos"
    target_id: Optional[str] = None
    task_id: Optional[str] = None
    max_tasks: Optional[int] = Field(default=None, ge=1, le=20)


class SelfUpgradeProposalDecisionIn(BaseModel):
    proposal_id: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1, description="approve|reject|hold")
    title: Optional[str] = None
    summary: Optional[str] = None
    version_bump: Optional[str] = None


class OpenClawConfigIn(BaseModel):
    enabled: Optional[bool] = None
    channel: Optional[str] = None
    target: Optional[str] = None
    gateway_mode: Optional[str] = None
    gateway_url: Optional[str] = None
    gateway_token: Optional[str] = None
    gateway_password: Optional[str] = None
    gateway_transport: Optional[str] = None
    gateway_state_dir: Optional[str] = None
    allow_insecure_private_ws: Optional[bool] = None
    path_patterns: Optional[list[str]] = None
    event_types: Optional[list[str]] = None
    exclude_event_types: Optional[list[str]] = None
    message_prefix: Optional[str] = None


class OpenClawReportTestIn(BaseModel):
    message: Optional[str] = None
    channel: Optional[str] = None
    target: Optional[str] = None
    path: Optional[str] = None
    dry_run: bool = False


class OpenClawSweepIn(BaseModel):
    dry_run: bool = False
    limit: int = Field(default=100, ge=1, le=500)


class RunStartIn(BaseModel):
    project_id: str = "teamos"
    workstream_id: str = "general"
    objective: str = Field(..., min_length=1)
    flow: Optional[str] = None
    # backward-compatible with old payload shape
    pipeline: Optional[str] = None
    # optional task binding; enables deterministic task/run consistency checks
    task_id: Optional[str] = None
    target_id: Optional[str] = None
    repo_path: Optional[str] = None
    repo_url: Optional[str] = None
    repo_locator: Optional[str] = None
    dry_run: bool = False
    force: bool = False
    trigger: Optional[str] = None


class ImprovementTargetIn(BaseModel):
    target_id: Optional[str] = None
    project_id: str = "teamos"
    display_name: Optional[str] = None
    repo_path: Optional[str] = None
    repo_url: Optional[str] = None
    repo_locator: Optional[str] = None
    default_branch: Optional[str] = None
    enabled: bool = True
    auto_discovery: bool = False
    auto_delivery: bool = False
    ship_enabled: bool = False
    workstream_id: str = "general"


class NodeRegisterIn(BaseModel):
    instance_id: str
    role_preference: str = "auto"  # brain|assistant|auto
    base_url: str = ""
    capabilities: list[str] = Field(default_factory=list)
    resources: dict[str, Any] = Field(default_factory=dict)
    agent_policy: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class NodeHeartbeatIn(BaseModel):
    instance_id: str


class TaskNewIn(BaseModel):
    title: str = Field(..., min_length=1)
    project_id: str
    repo_locator: Optional[str] = None
    create_repo_if_missing: bool = False
    visibility: str = "private"  # private|public
    org: Optional[str] = None
    workstreams: Optional[list[str]] = None
    required_capabilities: Optional[list[str]] = None
    mode: str = "auto"  # auto|bootstrap|upgrade
    dry_run: bool = True  # safety default: do not touch remotes


class RecoveryResumeIn(BaseModel):
    task_id: Optional[str] = None
    all: bool = False


@app.get("/healthz")
def healthz(response: Response):
    team_os_path = os.getenv("TEAM_OS_REPO_PATH", "/team-os")
    checks = _team_os_checks(team_os_path)
    crewai_info = crewai_runtime.probe_crewai()
    ok = (
        checks["exists"]
        and checks["specs_workflows_dir_exists"]
        and checks["specs_roles_dir_exists"]
        and checks["runtime_role_library_exists"]
        and checks["repo_improvement_team_spec_exists"]
        and checks["crewai_orchestrator_exists"]
        and bool(crewai_info.get("importable"))
    )
    db = {"backend": ("postgres" if (os.getenv("TEAMOS_DB_URL") or "").strip() else "sqlite"), "ok": True, "error": ""}
    try:
        # Minimal DB probe (no side effects).
        _ = DB.list_events(after_id=0, limit=1)
    except Exception as e:
        db["ok"] = False
        db["error"] = str(e)[:200]
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks, "crewai": crewai_info, "db": db, "redis_bus": redis_bus.describe()}


@app.get("/v1/status")
def v1_status():
    instance_id = ensure_instance_id()
    focus = load_focus()
    ws_root = str(_workspace_root())
    active_projects = _active_projects_summary()
    targets = improvement_store.list_targets()
    default_target = _default_local_improvement_target()
    default_target_id = str((default_target or {}).get("target_id") or "")
    default_target_project_id = str((default_target or {}).get("project_id") or "teamos").strip() or "teamos"

    runs = [r.__dict__ for r in DB.list_runs()]
    agents = [a.__dict__ for a in DB.list_agents()]

    tasks = _load_tasks_summary()
    task_run_sync = _task_run_sync_summary(tasks=tasks, runs=runs)
    crewai_info = crewai_runtime.probe_crewai()
    repo_improvement_state = crewai_self_upgrade._read_state(default_target_id) if default_target_id else {}
    proposals = crewai_self_upgrade.list_proposals()
    delivery_tasks = crewai_self_upgrade_delivery.list_delivery_tasks()
    delivery_summary = crewai_self_upgrade_delivery.delivery_summary()
    milestones = [m.__dict__ for m in list_milestones("teamos")]
    target_summaries: list[dict[str, Any]] = []
    for target in targets:
        tid = str(target.get("target_id") or "").strip()
        if not tid:
            continue
        target_project_id = str(target.get("project_id") or "")
        target_state = crewai_self_upgrade._read_state(tid)
        target_proposals = crewai_self_upgrade.list_proposals(target_id=tid, project_id=target_project_id)
        target_tasks = crewai_self_upgrade_delivery.list_delivery_tasks(project_id=target_project_id, target_id=tid)
        target_summaries.append(
            {
                "target_id": tid,
                "project_id": target_project_id,
                "display_name": str(target.get("display_name") or ""),
                "repo_locator": str(target.get("repo_locator") or ""),
                "last_run": target_state.get("last_run") if isinstance(target_state.get("last_run"), dict) else {},
                "proposal_counts": {
                    "total": len(target_proposals),
                    "pending": len([p for p in target_proposals if str(p.get("status") or "").strip().upper() not in ("REJECTED", "MATERIALIZED")]),
                },
                "delivery_counts": crewai_self_upgrade_delivery.delivery_summary(project_id=target_project_id, target_id=tid),
                "milestones": len([m for m in milestones if str(m.get("target_id") or "").strip() == tid]),
                "active_tasks": len([t for t in target_tasks if str(t.get("status") or "") in ("todo", "doing", "test", "release", "merge_conflict")]),
            }
        )
    openclaw_status = openclaw_reporter.detect_openclaw(probe_health=False)
    openclaw_status["state"] = openclaw_reporter.load_state()
    pending_proposals = [
        p
        for p in proposals
        if str(p.get("status") or "").strip().upper() not in ("REJECTED", "MATERIALIZED")
    ]

    pending = _pending_decisions()

    repo_improvement_payload = {
        "default_target_id": default_target_id,
        "last_run": repo_improvement_state.get("last_run") if isinstance(repo_improvement_state.get("last_run"), dict) else {},
        "backoff_until": str(repo_improvement_state.get("backoff_until") or ""),
        "loops": _repo_improvement_loop_state_snapshot(),
        "workflows": _repo_improvement_workflow_status_snapshot(
            target_id=default_target_id,
            project_id=default_target_project_id,
        ),
        "proposal_counts": {
            "total": len(proposals),
            "pending": len(pending_proposals),
            "feature": len([p for p in proposals if str(p.get("lane") or "").strip().lower() == "feature"]),
            "bug": len([p for p in proposals if str(p.get("lane") or "").strip().lower() == "bug"]),
            "process": len([p for p in proposals if str(p.get("lane") or "").strip().lower() == "process"]),
            "quality": len([p for p in proposals if str(p.get("lane") or "").strip().lower() == "quality"]),
        },
        "pending_proposals": pending_proposals[:20],
        "delivery": {
            "summary": delivery_summary,
            "active_tasks": [t for t in delivery_tasks if str(t.get("status") or "") in ("todo", "doing", "test", "release")][:20],
            "blocked_tasks": [t for t in delivery_tasks if str(t.get("status") or "") == "blocked"][:20],
        },
        "milestones": {
            "total": len(milestones),
            "active": len([m for m in milestones if str(m.get("state") or "") == "active"]),
            "blocked": len([m for m in milestones if str(m.get("state") or "") == "blocked"]),
            "release_candidate": len([m for m in milestones if str(m.get("state") or "") == "release-candidate"]),
            "items": milestones[:20],
        },
    }

    return {
        "instance_id": instance_id,
        "workspace_root": ws_root,
        "workspace_projects_count": len(_list_workspace_projects()),
        "current_focus": focus,
        "active_projects": active_projects,
        "active_runs": runs,
        "agents": agents,
        "tasks": tasks,
        "task_run_sync": task_run_sync,
        "crewai": crewai_info,
        "improvement_targets": targets,
        "improvement_target_summaries": target_summaries,
        "repo_improvement": repo_improvement_payload,
        "openclaw": openclaw_status,
        "pending_decisions": pending,
        "redis_bus": redis_bus.describe(),
    }


@app.get("/v1/agents")
def v1_agents(
    project_id: Optional[str] = None,
    workstream_id: Optional[str] = None,
    state: Optional[str] = None,
    role_id: Optional[str] = None,
):
    return {"agents": [a.__dict__ for a in DB.list_agents(project_id=project_id, workstream_id=workstream_id, state=state, role_id=role_id)]}


@app.get("/v1/tasks")
def v1_tasks(
    project_id: Optional[str] = None,
    workstream_id: Optional[str] = None,
    state: Optional[str] = None,
    owner_role: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    tasks = _load_tasks_summary()
    if project_id:
        tasks = [t for t in tasks if t.get("project_id") == project_id]
    if workstream_id:
        tasks = [t for t in tasks if t.get("workstream_id") == workstream_id]
    if state:
        tasks = [t for t in tasks if t.get("state") == state]
    if owner_role:
        tasks = [t for t in tasks if t.get("owner_role") == owner_role]
    total = len(tasks)
    items = tasks[offset : offset + limit]
    return {"total": total, "offset": offset, "limit": limit, "tasks": items}


@app.get("/v1/runs")
def v1_runs(project_id: Optional[str] = None, workstream_id: Optional[str] = None):
    return {"runs": [r.__dict__ for r in DB.list_runs(project_id=project_id, workstream_id=workstream_id)]}


@app.get("/v1/runs/{run_id}")
def v1_run_get(run_id: str):
    row = DB.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail={"error": "run_not_found", "run_id": run_id})
    return {"run": row.__dict__}


def _repo_improvement_run_logs_payload(run_id: str, *, limit: int = 200) -> dict[str, Any]:
    try:
        return improvement_store.persist_repo_improvement_run_logs(db=DB, run_id=run_id, limit=limit)
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": "run_not_found", "run_id": run_id}) from None


@app.get("/v1/repo_improvement/runs/{run_id}/logs")
@app.get("/v1/runs/{run_id}/logs")
def v1_run_logs(run_id: str, limit: int = Query(default=200, ge=1, le=1000)):
    return _repo_improvement_run_logs_payload(run_id, limit=limit)


@app.get("/v1/repo_improvement/runs/{run_id}/stream")
@app.get("/v1/runs/{run_id}/stream")
def v1_run_stream(
    run_id: str,
    after_id: int = Query(default=0, ge=0),
    event_limit: int = Query(default=200, ge=1, le=1000),
    poll_sec: float = Query(default=1.0, ge=0.2, le=10.0),
):
    row = DB.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail={"error": "run_not_found", "run_id": run_id})

    async def gen():
        import asyncio

        current_run = row
        task_id = _repo_improvement_task_id_for_run(current_run)
        last_event_id = max(0, int(after_id or 0))
        last_run_state = str(current_run.state or "").strip().upper()
        yield _sse_chunk(
            event="run",
            data={"run": current_run.__dict__, "task_id": task_id},
        )
        backlog, last_event_id = _repo_improvement_events_since(run=current_run, after_id=last_event_id, limit=event_limit)
        for item in backlog:
            yield _sse_chunk(event="runtime_event", data=item, event_id=int(item.get("id") or 0))
        agent_snapshots = _repo_improvement_agents_for_run(current_run)
        last_agent_signatures = {
            str(item.get("agent_id") or ""): (
                str(item.get("state") or ""),
                str(item.get("current_action") or ""),
                str(item.get("last_heartbeat") or ""),
            )
            for item in agent_snapshots
        }
        for item in agent_snapshots:
            yield _sse_chunk(event="agent", data=item)

        while True:
            current_run = DB.get_run(run_id)
            if not current_run:
                yield _sse_chunk(event="end", data={"run_id": run_id, "state": "MISSING"})
                break
            current_state = str(current_run.state or "").strip().upper()
            if current_state != last_run_state:
                task_id = _repo_improvement_task_id_for_run(current_run)
                yield _sse_chunk(event="run", data={"run": current_run.__dict__, "task_id": task_id})
                last_run_state = current_state

            events, last_event_id = _repo_improvement_events_since(run=current_run, after_id=last_event_id, limit=event_limit)
            for item in events:
                yield _sse_chunk(event="runtime_event", data=item, event_id=int(item.get("id") or 0))

            current_agents = _repo_improvement_agents_for_run(current_run)
            current_signatures = {}
            for item in current_agents:
                agent_id = str(item.get("agent_id") or "")
                signature = (
                    str(item.get("state") or ""),
                    str(item.get("current_action") or ""),
                    str(item.get("last_heartbeat") or ""),
                )
                current_signatures[agent_id] = signature
                if last_agent_signatures.get(agent_id) != signature:
                    yield _sse_chunk(event="agent", data=item)
            last_agent_signatures = current_signatures

            if current_state not in _RUN_STATE_ACTIVE:
                yield _sse_chunk(event="end", data={"run": current_run.__dict__, "task_id": _repo_improvement_task_id_for_run(current_run)})
                break

            yield b": keep-alive\n\n"
            await asyncio.sleep(float(poll_sec))

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/runs/start")
def v1_run_start(payload: RunStartIn):
    _require_leader_write()
    project_id = str(payload.project_id or "teamos").strip() or "teamos"
    workstream_id = str(payload.workstream_id or "general").strip() or "general"
    task_id = str(payload.task_id or "").strip()
    if task_id:
        task_exists = any(
            str(t.get("task_id") or "") == task_id and str(t.get("project_id") or "") == project_id
            for t in _load_tasks_summary()
        )
        if not task_exists:
            raise HTTPException(
                status_code=404,
                detail={"error": "task_not_found", "task_id": task_id, "project_id": project_id},
            )
    flow = crew_tools.resolve_run_request_flow(flow=payload.flow, pipeline=payload.pipeline)
    spec = crewai_orchestrator.RunSpec(
        project_id=project_id,
        workstream_id=workstream_id,
        objective=str(payload.objective),
        flow=flow,
        task_id=task_id,
        target_id=str(payload.target_id or "").strip(),
        repo_path=str(payload.repo_path or "").strip(),
        repo_url=str(payload.repo_url or "").strip(),
        repo_locator=str(payload.repo_locator or "").strip(),
        dry_run=bool(payload.dry_run),
        force=bool(payload.force),
        trigger=str(payload.trigger or ""),
    )
    out = crewai_orchestrator.run_once(db=DB, spec=spec, actor="crewai_orchestrator")
    _mark_panel_dirty(project_id)
    return out


@app.get("/v1/focus")
def v1_focus():
    return load_focus()


@app.post("/v1/focus")
def v1_focus_set(payload: FocusUpdate):
    f = save_focus(payload.model_dump(), source="api")
    DB.add_event(
        event_type="FOCUS_UPDATED",
        actor="control-plane",
        project_id=_default_project_id(),
        workstream_id=_default_workstream_id(),
        payload={"objective": f.get("objective"), "updated_at": f.get("updated_at"), "note": payload.note or ""},
    )
    _mark_panel_dirty()  # focus is global; refresh all configured panels
    return f


@app.get("/v1/auth/status")
def v1_auth_status():
    try:
        ok, msg = codex_llm.codex_login_status()
        return {"backend": "codex", "logged_in": ok, "message": msg}
    except codex_llm.CodexUnavailable as e:
        return {"backend": "codex", "logged_in": False, "message": str(e)}


@app.get("/v1/panel/github/config")
def v1_panel_github_config():
    """
    Returns mapping.yaml summary and panel URL list.
    """
    out: dict[str, Any] = {"mapping_path": str(github_projects_mapping_path()), "projects": []}
    try:
        m = load_mapping()
        out["mapping_sha256"] = m.sha256
        projects = (m.data.get("projects") or {}) if isinstance(m.data.get("projects"), dict) else {}
        for pid, cfg in projects.items():
            if not isinstance(cfg, dict):
                continue
            out["projects"].append(
                {
                    "project_id": pid,
                    "owner_type": cfg.get("owner_type"),
                    "owner": cfg.get("owner"),
                    "repo": cfg.get("repo"),
                    "project_number": cfg.get("project_number"),
                    "project_node_id": ("set" if str(cfg.get("project_node_id") or "").strip() else ""),
                    "project_url": cfg.get("project_url") or "",
                    "fields": {k: {"name": (v or {}).get("name"), "type": (v or {}).get("type"), "field_id": ("set" if str((v or {}).get("field_id") or "").strip() else "")} for k, v in ((cfg.get("fields") or {}) if isinstance(cfg.get("fields"), dict) else {}).items()},
                }
            )
    except PanelMappingError as e:
        out["error"] = str(e)
    return out


@app.get("/v1/panel/github/health")
def v1_panel_github_health(project_id: Optional[str] = None, include_github_rate_limit: bool = False):
    """
    Returns last sync run metadata (success/errors) and basic configuration hints.
    """
    pid = project_id or _default_project_id()
    last = DB.get_last_panel_sync(project_id=pid, panel_type="github_projects")
    summary = DB.get_panel_sync_summary(project_id=pid, panel_type="github_projects")

    needs_full_resync = False
    if not (summary.get("last_success")):
        needs_full_resync = True
    if last and (not last.get("ok")) and str(last.get("mode") or "").lower() == "incremental":
        needs_full_resync = True

    out: dict[str, Any] = {
        "project_id": pid,
        "last_sync": last,
        "summary": summary,
        "auto_sync": {
            "enabled": _env_truthy("TEAMOS_PANEL_GH_AUTO_SYNC", "0"),
            "interval_sec": int(os.getenv("TEAMOS_PANEL_GH_SYNC_INTERVAL_SEC", "60") or "60"),
            "debounce_sec": int(os.getenv("TEAMOS_PANEL_GH_SYNC_DEBOUNCE_SEC", "30") or "30"),
        },
        "writes_enabled": _panel_github_writes_enabled(),
        "needs_full_resync": needs_full_resync,
        "notes": [
            "GitHub Projects is a view-layer; truth source is local files + runtime DB.",
            "Use POST /v1/panel/github/sync for manual sync; enable auto-sync via env TEAMOS_PANEL_GH_AUTO_SYNC=1 (remote writes).",
        ],
    }

    # Optional: GitHub rate limit (remote read). Disabled by default.
    if include_github_rate_limit:
        try:
            tok = resolve_github_token()
            api_url = "https://api.github.com/graphql"
            try:
                m = load_mapping()
                api_url = str((m.data.get("github") or {}).get("graphql_api_url") or api_url).strip()
            except Exception:
                pass
            gh = GitHubGraphQL(token=tok, api_url=api_url)
            data = gh.graphql(RATE_LIMIT_QUERY, {}, timeout_sec=10)
            out["github_rate_limit"] = data.get("rateLimit") or {}
        except (GitHubAuthError, GitHubAPIError) as e:
            out["github_rate_limit_error"] = str(e)[:300]
        except Exception as e:
            out["github_rate_limit_error"] = str(e)[:300]

    return out


@app.post("/v1/panel/github/sync")
def v1_panel_github_sync(payload: PanelSyncIn):
    """
    Sync TeamOS truth -> GitHub Projects v2.
    """
    if not bool(payload.dry_run):
        _require_leader_write()
    if (not payload.dry_run) and (not _panel_github_writes_enabled()):
        DB.add_event(
            event_type="PANEL_SYNC_WRITE_BLOCKED",
            actor="control-plane",
            project_id=payload.project_id,
            workstream_id=_default_workstream_id(),
            payload={"panel": "github_projects", "mode": payload.mode},
        )
        raise HTTPException(status_code=403, detail="GitHub panel writes are disabled. Set TEAMOS_PANEL_GH_WRITE_ENABLED=1 to allow remote writes.")

    svc = GitHubProjectsPanelSync(db=DB)
    ts_start = _utc_now_iso()
    ok = True
    err = ""
    res: dict[str, Any] = {}
    try:
        res = svc.sync(project_id=payload.project_id, mode=payload.mode, dry_run=bool(payload.dry_run))
        DB.add_event(
            event_type="PANEL_SYNC_MANUAL_OK" if not payload.dry_run else "PANEL_SYNC_DRY_RUN_OK",
            actor="user",
            project_id=payload.project_id,
            workstream_id=_default_workstream_id(),
            payload={"panel": "github_projects", "mode": payload.mode, "dry_run": bool(payload.dry_run), "stats": res.get("stats") or {}},
        )
        return res
    except Exception as e:
        ok = False
        err = str(e)
        DB.add_event(
            event_type="PANEL_SYNC_MANUAL_FAIL" if not payload.dry_run else "PANEL_SYNC_DRY_RUN_FAIL",
            actor="user",
            project_id=payload.project_id,
            workstream_id=_default_workstream_id(),
            payload={"panel": "github_projects", "mode": payload.mode, "dry_run": bool(payload.dry_run), "error": err[:500]},
        )
        raise
    finally:
        try:
            stats = (res.get("stats") or {}) if isinstance(res, dict) else {}
            DB.record_panel_sync_run(
                project_id=payload.project_id,
                panel_type="github_projects",
                mode=str(payload.mode),
                dry_run=bool(payload.dry_run),
                ok=ok,
                stats=stats if isinstance(stats, dict) else {"_raw": str(stats)},
                error=err,
                ts_start=ts_start,
                ts_end=_utc_now_iso(),
            )
        except Exception:
            pass


@app.post("/v1/chat")
def v1_chat(payload: ChatIn):
    project_id = payload.project_id or _default_project_id()
    workstream_id = payload.workstream_id or _default_workstream_id()
    msg_type = (payload.message_type or "GENERAL").strip().upper()

    _append_conversation(project_id, payload.model_dump())
    DB.add_event(
        event_type="CHAT_MESSAGE",
        actor="user",
        project_id=project_id,
        workstream_id=workstream_id,
        payload={"message_type": msg_type, "run_id": payload.run_id or "", "len": len(payload.message)},
    )

    actions: list[str] = []
    pending: list[dict[str, Any]] = []
    response_lines: list[str] = []

    if msg_type in ("PAUSE", "RESUME", "STOP") and payload.run_id:
        desired = {"PAUSE": "PAUSED", "RESUME": "RUNNING", "STOP": "STOPPED"}[msg_type]
        run = DB.get_run(payload.run_id)
        if run:
            DB.update_run_state(run_id=payload.run_id, state=desired)
        else:
            DB.upsert_run(
                run_id=payload.run_id,
                project_id=project_id,
                workstream_id=workstream_id,
                objective=f"(unknown objective) run_id={payload.run_id}",
                state=desired,
            )
        DB.add_event(
            event_type="RUN_STATE_UPDATED",
            actor="user",
            project_id=project_id,
            workstream_id=workstream_id,
            payload={"run_id": payload.run_id, "state": desired},
        )
        _mark_panel_dirty(project_id)
        try:
            emit_n8n_event(
                "task_state_changed",
                project_id=project_id,
                workstream_id=workstream_id,
                payload={"run_id": payload.run_id, "state": desired},
            )
        except Exception:
            pass
        actions.append(f"run_state={desired}")
        response_lines.append(f"run_id={payload.run_id} state={desired}")
        return {"response_text": "\n".join(response_lines).strip() + "\n", "actions_taken": actions, "pending_decisions": pending}

    if msg_type == "NEW_REQUIREMENT":
        _require_leader_write()
        out = _handle_new_requirement(
            project_id=project_id,
            workstream_id=workstream_id,
            requirement_text=payload.message,
            source="chat",
        )
        actions += out["actions_taken"]
        pending += out["pending_decisions"]
        response_lines.append(out["summary"])
    else:
        day = _utc_now_iso().split("T", 1)[0]
        conv_path = _conversations_dir(project_id, ensure=True) / f"{day}.jsonl"
        response_lines.append(
            "\n".join(
                [
                    "Message recorded.",
                    f"- project_id={project_id} workstream_id={workstream_id} message_type={msg_type}",
                    f"- conversation_log={conv_path}",
                    "Tip: use `/req <text>` in CLI or set message_type=NEW_REQUIREMENT to register requirements with conflict check.",
                ]
            )
        )

    return {"response_text": "\n".join(response_lines).strip() + "\n", "actions_taken": actions, "pending_decisions": pending}


def _baseline_versions(req_dir: Path) -> list[int]:
    d = req_dir / "baseline"
    if not d.exists():
        return []
    out: list[int] = []
    for p in sorted(d.glob("original_description_v*.md")):
        name = p.name
        try:
            v = int(name.split("_v", 1)[1].split(".md", 1)[0])
            out.append(v)
        except Exception:
            continue
    return sorted(set(out))


@app.get("/v1/requirements/show")
def v1_requirements_show(scope: str = Query(..., min_length=1)):
    project_id = _parse_scope_to_project_id(scope)
    req_dir = _requirements_dir(project_id, ensure=False)
    y = req_dir / "requirements.yaml"
    reqs: list[dict[str, Any]] = []
    if y.exists():
        try:
            data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
            reqs = list(data.get("requirements") or [])
        except Exception:
            reqs = []
    raw = req_dir / "raw_inputs.jsonl"
    raw_count = 0
    if raw.exists():
        try:
            raw_count = sum(1 for ln in raw.read_text(encoding="utf-8").splitlines() if ln.strip())
        except Exception:
            raw_count = 0
    return {
        "scope": scope,
        "project_id": project_id,
        "requirements_dir": str(req_dir),
        "baseline_versions": _baseline_versions(req_dir),
        "raw_inputs_path": str(raw),
        "raw_inputs_count": raw_count,
        "requirements": reqs,
        "conflicts_dir": str(req_dir / "conflicts"),
        "changelog_path": str(req_dir / "CHANGELOG.md"),
        "requirements_yaml": str(req_dir / "requirements.yaml"),
        "requirements_md": str(req_dir / "REQUIREMENTS.md"),
    }


@app.post("/v1/requirements/verify")
def v1_requirements_verify(payload: RequirementVerifyV2In):
    project_id = _parse_scope_to_project_id(payload.scope)
    req_dir = _requirements_dir(project_id, ensure=False)
    return verify_requirements_raw_first(req_dir, project_id=project_id)


@app.post("/v1/requirements/rebuild")
def v1_requirements_rebuild(payload: RequirementRebuildV2In):
    _require_leader_write()
    project_id = _parse_scope_to_project_id(payload.scope)
    req_dir = _requirements_dir(project_id, ensure=True)
    return rebuild_requirements_md(req_dir, project_id=project_id)


@app.get("/v1/requirements/baseline/show")
def v1_requirements_baseline_show(scope: str = Query(..., min_length=1), max_chars: int = 4000):
    project_id = _parse_scope_to_project_id(scope)
    req_dir = _requirements_dir(project_id, ensure=False)
    d = req_dir / "baseline"
    items: list[dict[str, Any]] = []
    if d.exists():
        for p in sorted(d.glob("original_description_v*.md")):
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                txt = ""
            items.append(
                {
                    "path": str(p),
                    "name": p.name,
                    "text_preview": (txt[: max(0, int(max_chars))] if txt else ""),
                }
            )
    return {"scope": scope, "project_id": project_id, "baselines": items}


@app.post("/v1/requirements/baseline/set-v2")
def v1_requirements_baseline_set_v2(payload: BaselineSetV2In):
    _require_leader_write()
    project_id = _parse_scope_to_project_id(payload.scope)
    req_dir = _requirements_dir(project_id, ensure=True)
    out = propose_baseline_v2(
        req_dir,
        project_id=project_id,
        new_baseline_text=payload.text,
        reason=payload.reason,
        channel="api",
        user="user",
    )
    DB.add_event(
        event_type="BASELINE_V2_PROPOSED",
        actor="user",
        project_id=project_id,
        workstream_id=_default_workstream_id(),
        payload=out,
    )
    _mark_panel_dirty(project_id)
    return out


@app.post("/v1/requirements/add")
def v1_requirements_add_v2(payload: RequirementAddV2In):
    _require_leader_write()
    project_id = _parse_scope_to_project_id(payload.scope)
    workstream_id = payload.workstream_id or _default_workstream_id()
    out = _handle_new_requirement(
        project_id=project_id,
        workstream_id=workstream_id,
        requirement_text=payload.text,
        priority=payload.priority or "P2",
        rationale=payload.rationale or "",
        constraints=payload.constraints,
        acceptance=payload.acceptance,
        source=payload.source or "api",
    )
    out["scope"] = payload.scope
    return out


@app.post("/v1/requirements/import")
def v1_requirements_import_v2(payload: RequirementImportV2In):
    _require_leader_write()
    project_id = _parse_scope_to_project_id(payload.scope)
    workstream_id = payload.workstream_id or _default_workstream_id()
    out = _handle_new_requirement(
        project_id=project_id,
        workstream_id=workstream_id,
        requirement_text=payload.content_text,
        priority="P2",
        rationale="import",
        constraints=None,
        acceptance=None,
        source=payload.source or "import",
    )
    out["scope"] = payload.scope
    out["import_filename"] = payload.filename or ""
    return out


@app.post("/v1/requirements")
def v1_requirements_add(payload: RequirementIn):
    _require_leader_write()
    project_id = payload.project_id
    workstream_id = payload.workstream_id or _default_workstream_id()
    out = _handle_new_requirement(
        project_id=project_id,
        workstream_id=workstream_id,
        requirement_text=payload.requirement_text,
        priority=payload.priority or "P2",
        rationale=payload.rationale or "",
        constraints=payload.constraints,
        acceptance=payload.acceptance,
        source=payload.source or "api",
    )
    return out


@app.get("/v1/requirements")
def v1_requirements_list(project_id: str):
    req_dir = _requirements_dir(project_id, ensure=False)
    y = req_dir / "requirements.yaml"
    if not y.exists():
        return {"project_id": project_id, "requirements": [], "conflicts_dir": str(req_dir / "conflicts")}
    with y.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {"project_id": project_id, "requirements_dir": str(req_dir), "requirements": data.get("requirements") or []}


@app.get("/v1/hub/status")
def v1_hub_status():
    env = _hub_env()
    pg_host = str(env.get("PG_BIND_IP") or "127.0.0.1")
    pg_port = int(str(env.get("PG_PORT") or "5432"))
    redis_enabled = str(env.get("HUB_REDIS_ENABLED") or "1") == "1"
    redis_host = str(env.get("REDIS_BIND_IP") or "127.0.0.1")
    redis_port = int(str(env.get("REDIS_PORT") or "6379"))
    mig_rows = _db_rows("SELECT version, applied_at FROM schema_migrations ORDER BY version DESC LIMIT 20")
    return {
        "hub_root": str(_hub_root()),
        "postgres": {
            "bind_ip": pg_host,
            "port": pg_port,
            "tcp_open": _tcp_open(pg_host, pg_port),
            "connections": _db_rows("SELECT count(*)::int AS count FROM pg_stat_activity"),
        },
        "redis": {
            "enabled": redis_enabled,
            "bind_ip": redis_host,
            "port": redis_port,
            "tcp_open": _tcp_open(redis_host, redis_port) if redis_enabled else False,
        },
        "migrations": mig_rows,
        "approvals_pending": _db_rows("SELECT count(*)::int AS count FROM approvals WHERE status='REQUESTED'"),
        "locks_held": _db_rows("SELECT count(*)::int AS count FROM locks WHERE state='HELD'"),
    }


@app.get("/v1/hub/migrations")
def v1_hub_migrations():
    rows = _db_rows("SELECT version, applied_at FROM schema_migrations ORDER BY version ASC")
    return {"migrations": rows}


@app.get("/v1/hub/locks")
def v1_hub_locks(limit: int = Query(default=100, ge=1, le=500)):
    rows = _db_rows("SELECT lock_key, backend, holder, lease_ttl_sec, acquired_at, heartbeat_at, expires_at, state FROM locks ORDER BY heartbeat_at DESC LIMIT %s", (int(limit),))
    return {"locks": rows}


@app.get("/v1/hub/approvals")
def v1_hub_approvals(limit: int = Query(default=100, ge=1, le=500)):
    rows = _db_rows(
        """
        SELECT approval_id, task_id, action_kind, action_summary, risk_level, category, status, requested_by, requested_at, decided_by, decided_at, decision_engine
        FROM approvals
        ORDER BY requested_at DESC
        LIMIT %s
        """,
        (int(limit),),
    )
    return {"approvals": rows}


@app.get("/v1/nodes")
def v1_nodes():
    return {"nodes": [n.__dict__ for n in DB.list_nodes()]}


@app.post("/v1/nodes/register")
def v1_nodes_register(payload: NodeRegisterIn):
    DB.upsert_node(
        instance_id=str(payload.instance_id),
        role_preference=str(payload.role_preference or "auto"),
        base_url=str(payload.base_url or ""),
        capabilities=list(payload.capabilities or []),
        resources=dict(payload.resources or {}),
        agent_policy=dict(payload.agent_policy or {}),
        tags=list(payload.tags or []),
    )
    DB.add_event(
        event_type="NODE_REGISTERED",
        actor="node",
        project_id=_default_project_id(),
        workstream_id=_default_workstream_id(),
        payload={"instance_id": payload.instance_id, "role_preference": payload.role_preference, "capabilities": payload.capabilities, "tags": payload.tags},
    )
    # Best-effort: update GitHub nodes registry (remote write gated by cluster config/env).
    try:
        cfg = cluster_manager.load_cluster_config()
        y = yaml.safe_dump(
            {
                "instance_id": payload.instance_id,
                "role_preference": payload.role_preference,
                "heartbeat_at": _utc_now_iso(),
                "capabilities": payload.capabilities,
                "resources": payload.resources,
                "agent_policy": payload.agent_policy,
                "tags": payload.tags,
            },
            sort_keys=False,
            allow_unicode=True,
        )
        cluster_manager.upsert_node_registry_comment(cfg, instance_id=payload.instance_id, body_yaml=y)
    except Exception:
        pass
    return {"ok": True, "instance_id": payload.instance_id}


@app.post("/v1/nodes/heartbeat")
def v1_nodes_heartbeat(payload: NodeHeartbeatIn):
    DB.heartbeat_node(instance_id=str(payload.instance_id))
    DB.add_event(
        event_type="NODE_HEARTBEAT",
        actor="node",
        project_id=_default_project_id(),
        workstream_id=_default_workstream_id(),
        payload={"instance_id": payload.instance_id},
    )
    # Best-effort: refresh GitHub nodes registry heartbeat (remote write gated).
    try:
        cfg = cluster_manager.load_cluster_config()
        y = yaml.safe_dump({"instance_id": payload.instance_id, "heartbeat_at": _utc_now_iso()}, sort_keys=False, allow_unicode=True)
        cluster_manager.upsert_node_registry_comment(cfg, instance_id=payload.instance_id, body_yaml=y)
    except Exception:
        pass
    return {"ok": True}


@app.get("/v1/cluster/status")
def v1_cluster_status():
    instance_id = ensure_instance_id()
    base_url = os.getenv("CONTROL_PLANE_BASE_URL", "").strip()
    leader: dict[str, Any] = {"leader_instance_id": instance_id, "leader_base_url": base_url, "backend": "local", "lease_expires_at": ""}
    try:
        cfg = cluster_manager.load_cluster_config()
        cur = cluster_manager.read_leader(cfg)
        if cur and cur.leader_instance_id:
            leader = cur.__dict__
    except Exception:
        pass
    nodes = [n.__dict__ for n in DB.list_nodes()]
    llm_profile: dict[str, Any] = {}
    leader_qualification: dict[str, Any] = {}
    try:
        llm_profile = cluster_manager.local_llm_profile()
        allow = cluster_manager.load_central_model_allowlist()
        leader_qualification = cluster_manager.qualify_leader(allowlist=allow, profile=llm_profile)
    except Exception:
        pass
    return {
        "leader": leader,
        "llm_profile": llm_profile,
        "leader_qualification": leader_qualification,
        "nodes": nodes,
        "active_agents": [a.__dict__ for a in DB.list_agents()],
        "active_tasks": _load_tasks_summary(),
        "focus": load_focus(),
        "pending_decisions": _pending_decisions(),
        "panel": {"github_projects_mapping": str(github_projects_mapping_path())},
    }


@app.post("/v1/cluster/elect/attempt")
def v1_cluster_elect_attempt():
    instance_id = ensure_instance_id()
    cfg = cluster_manager.load_cluster_config()
    base_url = os.getenv("CONTROL_PLANE_BASE_URL", "").strip()
    try:
        out = cluster_manager.attempt_elect(cfg, instance_id=instance_id, base_url=base_url)
        DB.add_event(event_type="CLUSTER_ELECT_ATTEMPT", actor="control-plane", project_id="teamos", workstream_id=_default_workstream_id(), payload=out)
        return out
    except Exception as e:
        DB.add_event(event_type="CLUSTER_ELECT_FAILED", actor="control-plane", project_id="teamos", workstream_id=_default_workstream_id(), payload={"error": str(e)[:300]})
        return {"success": False, "reason": str(e)[:300], "leader": {"leader_instance_id": instance_id, "backend": "local"}}


def _ts_compact_utc() -> str:
    return _utc_now_iso().replace(":", "").replace("-", "")


@app.post("/v1/tasks/new")
def v1_tasks_new(payload: TaskNewIn):
    _require_leader_write()
    # Safety: do not touch remotes by default.
    if payload.create_repo_if_missing and not (payload.repo_locator or "").strip():
        # Repo creation is high risk -> require explicit approval and remote-write enable.
        raise HTTPException(status_code=412, detail="Repo creation requires explicit approval (high risk). Provide repo_locator or enable approved flow.")

    mode = (payload.mode or "auto").strip().lower()
    if mode not in ("auto", "bootstrap", "upgrade"):
        mode = "auto"

    workstreams = [str(x).strip() for x in (payload.workstreams or ["general"]) if str(x).strip()] or ["general"]
    wsid = str(workstreams[0])
    scope = _scope_from_project_id(str(payload.project_id or "teamos"))
    try:
        delegated = crew_tools.run_task_create_pipeline(
            repo_root=team_os_root(),
            workspace_root=_workspace_root(),
            scope=scope,
            title=str(payload.title),
            workstreams=workstreams,
            mode=mode,
            # Keep legacy behavior: task scaffold is always materialized locally.
            # This endpoint never performs remote writes.
            dry_run=False,
        )
    except crew_tools.CrewToolsError as e:
        raise HTTPException(status_code=400, detail={"error": "TASK_CREATE_PIPELINE_FAILED", "message": str(e)})

    created = delegated.get("result") or {}
    step = delegated.get("step") or {}
    task_id = str(created.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=500, detail={"error": "TASK_CREATE_PIPELINE_INVALID_OUTPUT", "message": "missing task_id"})
    event_payload = {
        "task_id": task_id,
        "mode": mode,
        "scope": scope,
        "dry_run": bool(payload.dry_run),
        "repo_locator": (payload.repo_locator or "")[:120],
        "write_delegate": step.get("write_delegate") or {},
        "evidence": "truth-source writes are delegated to scripts/pipelines/task_create.py",
    }
    DB.add_event(
        event_type="TASK_NEW",
        actor="user",
        project_id=payload.project_id,
        workstream_id=wsid,
        payload=event_payload,
    )
    _publish_redis_event(
        event_type="TASK_NEW",
        actor="user",
        project_id=payload.project_id,
        workstream_id=wsid,
        payload=event_payload,
    )
    _mark_panel_dirty(payload.project_id)
    pending: list[dict[str, Any]] = []
    if mode == "upgrade":
        pending.append(
            {
                "type": "REPO_UNDERSTANDING_GATE",
                "project_id": payload.project_id,
                "task_id": task_id,
                "message": "mode=upgrade requires docs/product/teamos/REPO_UNDERSTANDING.md before any code changes.",
                "artifact_template": "templates/content/repo_understanding.md",
            }
        )
    return {
        "task_id": task_id,
        "ledger_path": str(created.get("ledger_path") or ""),
        "logs_dir": str(created.get("logs_dir") or ""),
        "pending_decisions": pending,
        "write_delegate": step.get("write_delegate") or {},
    }


@app.post("/v1/recovery/scan")
def v1_recovery_scan():
    _require_leader_write()

    def _pending_approvals(task_id: str) -> list[dict[str, Any]]:
        dsn = str(os.getenv("TEAMOS_DB_URL") or "").strip()
        if not dsn or not (dsn.startswith("postgres://") or dsn.startswith("postgresql://")):
            return []
        try:
            import psycopg  # type: ignore
            from psycopg.rows import dict_row  # type: ignore
        except Exception:
            return []
        try:
            conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3)
        except Exception:
            return []
        try:
            with conn.cursor() as cur:
                rows = cur.execute(
                    """
                    SELECT approval_id, status, category, risk_level, action_kind, requested_at
                    FROM approvals
                    WHERE task_id=%s AND status='REQUESTED'
                    ORDER BY requested_at DESC
                    LIMIT 50
                    """,
                    (str(task_id),),
                ).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows or []:
                d = dict(r)
                # psycopg may return datetime objects for requested_at
                if d.get("requested_at") is not None:
                    d["requested_at"] = str(d["requested_at"])
                out.append(d)
            return out
        except Exception:
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _gates_for_task(t: dict[str, Any]) -> list[dict[str, Any]]:
        gates: list[dict[str, Any]] = []
        state = str(t.get("state") or "").strip().lower()
        if state == "blocked":
            gates.append({"type": "BLOCKED", "reason": "task status=blocked"})
        if bool(t.get("need_pm_decision")):
            gates.append({"type": "NEED_PM_DECISION", "reason": "need_pm_decision=true"})
        approvals = _pending_approvals(str(t.get("task_id") or ""))
        if approvals:
            gates.append({"type": "WAITING_APPROVAL", "reason": "pending approvals", "count": len(approvals), "sample": approvals[:3]})
        return gates

    # Deterministic scan: list non-closed tasks and write a local snapshot.
    tasks = _load_tasks_summary()
    active0 = [t for t in tasks if str(t.get("state") or "").lower() not in ("closed", "done")]
    active = []
    for t in active0:
        t2 = dict(t)
        t2["gates"] = _gates_for_task(t2)
        active.append(t2)
    active.sort(key=lambda x: (str(x.get("project_id") or ""), str(x.get("workstream_id") or ""), str(x.get("task_id") or "")))
    snap_dir = runtime_state_root() / "audit" / "recovery"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = _ts_compact_utc()
    path = snap_dir / f"recovery_{ts}.md"
    lines = ["# Recovery Scan", "", f"- ts: {_utc_now_iso()}", f"- active_tasks: {len(active)}", ""]
    for t in active[:200]:
        gates = t.get("gates") or []
        g = ",".join([str(x.get("type") or "") for x in (gates if isinstance(gates, list) else []) if str(x.get("type") or "").strip()])
        lines.append(f"- {t.get('task_id')} state={t.get('state')} project={t.get('project_id')} workstream={t.get('workstream_id')} gates={g}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    DB.add_event(event_type="RECOVERY_SCAN", actor="control-plane", project_id=_default_project_id(), workstream_id=_default_workstream_id(), payload={"active_tasks": len(active), "snapshot": str(path)})
    return {"active_tasks": active, "snapshot_path": str(path)}


@app.post("/v1/recovery/resume")
def v1_recovery_resume(payload: RecoveryResumeIn):
    _require_leader_write()

    def _pending_approvals(task_id: str) -> list[dict[str, Any]]:
        dsn = str(os.getenv("TEAMOS_DB_URL") or "").strip()
        if not dsn or not (dsn.startswith("postgres://") or dsn.startswith("postgresql://")):
            return []
        try:
            import psycopg  # type: ignore
            from psycopg.rows import dict_row  # type: ignore
        except Exception:
            return []
        try:
            conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3)
        except Exception:
            return []
        try:
            with conn.cursor() as cur:
                rows = cur.execute(
                    "SELECT approval_id, status, category, action_kind, requested_at FROM approvals WHERE task_id=%s AND status='REQUESTED' ORDER BY requested_at DESC LIMIT 50",
                    (str(task_id),),
                ).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows or []:
                d = dict(r)
                if d.get("requested_at") is not None:
                    d["requested_at"] = str(d["requested_at"])
                out.append(d)
            return out
        except Exception:
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _gates_for_task(t: dict[str, Any]) -> list[dict[str, Any]]:
        gates: list[dict[str, Any]] = []
        state = str(t.get("state") or "").strip().lower()
        if state in ("closed", "done"):
            gates.append({"type": "CLOSED", "reason": "already closed"})
            return gates
        if state == "blocked":
            gates.append({"type": "BLOCKED", "reason": "task status=blocked"})
        if bool(t.get("need_pm_decision")):
            gates.append({"type": "NEED_PM_DECISION", "reason": "need_pm_decision=true"})
        approvals = _pending_approvals(str(t.get("task_id") or ""))
        if approvals:
            gates.append({"type": "WAITING_APPROVAL", "reason": "pending approvals", "count": len(approvals)})
        return gates

    tasks = _load_tasks_summary()
    active = [t for t in tasks if str(t.get("state") or "").lower() not in ("closed", "done")]
    target: list[dict[str, Any]] = []
    if payload.all:
        target = active
    elif payload.task_id:
        target = [t for t in active if str(t.get("task_id") or "") == str(payload.task_id)]

    resumed: list[str] = []
    resumed_details: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for t in target:
        gates = _gates_for_task(t)
        if gates:
            skipped.append({"task_id": t.get("task_id"), "gates": gates})
            continue

        ledger_path = Path(str(t.get("ledger_path") or "")).expanduser()
        if ledger_path.exists():
            try:
                task_doc = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or {}
            except Exception:
                task_doc = {}
        else:
            task_doc = {}

        orchestration = (task_doc.get("orchestration") or {}) if isinstance(task_doc, dict) else {}
        if (
            isinstance(orchestration, dict)
            and str(orchestration.get("engine") or "").strip().lower() == "crewai"
            and _repo_improvement_flow(orchestration.get("flow"))
        ):
            delivery = _run_self_upgrade_delivery_iteration(
                actor="control-plane.recovery",
                project_id=str(t.get("project_id") or "teamos"),
                task_id=str(t.get("task_id") or ""),
                dry_run=False,
                force=True,
                max_tasks=1,
            )
            resumed.append(str(t.get("task_id") or ""))
            resumed_details.append(
                {
                    "task_id": str(t.get("task_id") or ""),
                    "mode": "repo_improvement_delivery",
                    "result": delivery,
                }
            )
            continue

        run_id = f"run-{t.get('task_id')}"
        DB.upsert_run(run_id=run_id, project_id=str(t.get("project_id") or ""), workstream_id=str(t.get("workstream_id") or ""), objective=str(t.get("title") or ""), state="RUNNING")

        existing = DB.list_agents(project_id=str(t.get("project_id") or ""), workstream_id=str(t.get("workstream_id") or ""), role_id="Process-Guardian")
        if not any(str(a.task_id) == str(t.get("task_id") or "") and str(a.state).upper() == "RUNNING" for a in existing):
            _ = DB.register_agent(
                role_id="Process-Guardian",
                project_id=str(t.get("project_id") or ""),
                workstream_id=str(t.get("workstream_id") or ""),
                task_id=str(t.get("task_id") or ""),
                state="RUNNING",
                current_action="recovery placeholder (no executor wired)",
            )
        resumed.append(str(t.get("task_id") or ""))
        resumed_details.append({"task_id": str(t.get("task_id") or ""), "mode": "placeholder"})

    DB.add_event(event_type="RECOVERY_RESUME", actor="control-plane", project_id=_default_project_id(), workstream_id=_default_workstream_id(), payload={"resumed": resumed, "skipped": skipped})
    _mark_panel_dirty()
    return {"ok": True, "resumed": resumed, "resumed_details": resumed_details, "skipped": skipped}


@app.post("/v1/repo_improvement/run")
@app.post("/v1/self_upgrade/run")
def v1_repo_improvement_run(payload: SelfUpgradeIn):
    _require_leader_write()
    project_id = str(payload.project_id or "teamos").strip() or "teamos"
    workstream_id = str(payload.workstream_id or "general").strip() or "general"
    return _run_self_upgrade_iteration(
        actor="repo_improvement_api",
        project_id=project_id,
        workstream_id=workstream_id,
        objective=str(payload.objective or "Run CrewAI repo-improvement for the target repository").strip(),
        target_id=str(payload.target_id or "").strip(),
        repo_path=str(payload.repo_path or "").strip(),
        repo_url=str(payload.repo_url or "").strip(),
        repo_locator=str(payload.repo_locator or "").strip(),
        dry_run=bool(payload.dry_run),
        force=bool(payload.force),
        trigger=str(payload.trigger or "api"),
    )


@app.get("/v1/improvement/targets")
def v1_improvement_targets(project_id: str = Query(default=""), enabled_only: bool = False):
    targets = improvement_store.list_targets(project_id=str(project_id or "").strip(), enabled_only=bool(enabled_only))
    return {"total": len(targets), "targets": targets}


@app.post("/v1/improvement/targets")
def v1_improvement_targets_upsert(payload: ImprovementTargetIn):
    _require_leader_write()
    target = improvement_store.upsert_target(payload.model_dump(exclude_none=True))
    DB.add_event(
        event_type="IMPROVEMENT_TARGET_UPSERTED",
        actor="improvement_target_api",
        project_id=str(target.get("project_id") or "teamos").strip() or "teamos",
        workstream_id=str(target.get("workstream_id") or "general").strip() or "general",
        payload={"target": target},
    )
    return {"ok": True, "target": target}


@app.get("/v1/repo_improvement/proposals")
@app.get("/v1/self_upgrade/proposals")
def v1_repo_improvement_proposals(
    target_id: str = Query(default=""),
    project_id: str = Query(default=""),
    lane: str = Query(default=""),
    status: str = Query(default=""),
):
    proposals = crewai_self_upgrade.list_proposals(target_id=target_id, project_id=project_id, lane=lane, status=status)
    return {"total": len(proposals), "proposals": proposals}


@app.post("/v1/repo_improvement/proposals/decide")
@app.post("/v1/self_upgrade/proposals/decide")
def v1_repo_improvement_proposals_decide(payload: SelfUpgradeProposalDecisionIn):
    _require_leader_write()
    try:
        proposal = crewai_self_upgrade.decide_proposal(
            proposal_id=payload.proposal_id,
            action=payload.action,
            title=str(payload.title or "").strip(),
            summary=str(payload.summary or "").strip(),
            version_bump=str(payload.version_bump or "").strip(),
        )
    except crewai_self_upgrade.SelfUpgradeError as e:
        raise HTTPException(status_code=400, detail={"error": "repo_improvement_proposal_decision_failed", "message": str(e)})
    project_id = str(proposal.get("project_id") or "teamos").strip() or "teamos"
    workstream_id = str(proposal.get("workstream_id") or "general").strip() or "general"
    DB.add_event(
        event_type="REPO_IMPROVEMENT_PROPOSAL_DECIDED",
        actor="repo_improvement_api",
        project_id=project_id,
        workstream_id=workstream_id,
        payload={"proposal": proposal},
    )
    _mark_panel_dirty(project_id)
    return {"ok": True, "proposal": proposal}


@app.post("/v1/repo_improvement/discussions/sync")
@app.post("/v1/self_upgrade/discussions/sync")
def v1_repo_improvement_discussions_sync():
    _require_leader_write()
    out = _run_self_upgrade_discussion_sync(actor="repo_improvement_discussion_api")
    return {"ok": True, **out}


@app.get("/v1/repo_improvement/milestones")
@app.get("/v1/self_upgrade/milestones")
def v1_repo_improvement_milestones(
    project_id: str = Query(default="teamos"),
    target_id: str = Query(default=""),
    state: str = Query(default=""),
):
    items = [m.__dict__ for m in list_milestones(str(project_id or "teamos").strip() or "teamos")]
    if target_id:
        items = [m for m in items if str(m.get("target_id") or "").strip() == str(target_id).strip()]
    state_filter = str(state or "").strip().lower()
    if state_filter:
        items = [m for m in items if str(m.get("state") or "").strip().lower() == state_filter]
    return {"total": len(items), "milestones": items}


@app.get("/v1/openclaw/status")
def v1_openclaw_status():
    out = openclaw_reporter.detect_openclaw()
    out["state"] = openclaw_reporter.load_state()
    return out


@app.get("/v1/openclaw/config")
def v1_openclaw_config():
    return {"config": openclaw_reporter.load_config()}


@app.post("/v1/openclaw/config")
def v1_openclaw_config_update(payload: OpenClawConfigIn):
    _require_leader_write()
    patch = payload.model_dump(exclude_none=True)
    config = openclaw_reporter.save_config(patch)
    DB.add_event(
        event_type="OPENCLAW_CONFIG_UPDATED",
        actor="openclaw_api",
        project_id="teamos",
        workstream_id=_default_workstream_id(),
        payload={"config": config},
    )
    return {"ok": True, "config": config, "status": openclaw_reporter.detect_openclaw()}


@app.post("/v1/openclaw/report/test")
def v1_openclaw_report_test(payload: OpenClawReportTestIn):
    _require_leader_write()
    out = openclaw_reporter.report_manual(
        message=str(payload.message or "").strip() or "Team OS OpenClaw test message",
        channel=str(payload.channel or "").strip(),
        target=str(payload.target or "").strip(),
        path=str(payload.path or "").strip(),
        dry_run=bool(payload.dry_run),
    )
    return out


@app.post("/v1/openclaw/sweep")
def v1_openclaw_sweep(payload: OpenClawSweepIn):
    _require_leader_write()
    return openclaw_reporter.sweep_events(db=DB, dry_run=bool(payload.dry_run), limit=int(payload.limit or 100))


@app.get("/v1/repo_improvement/delivery/tasks")
@app.get("/v1/self_upgrade/delivery/tasks")
def v1_repo_improvement_delivery_tasks(
    project_id: str = Query(default=""),
    target_id: str = Query(default=""),
    status: str = Query(default=""),
):
    tasks = crewai_self_upgrade_delivery.list_delivery_tasks(project_id=project_id, target_id=target_id, status=status)
    return {
        "total": len(tasks),
        "tasks": tasks,
        "summary": crewai_self_upgrade_delivery.delivery_summary(project_id=project_id, target_id=target_id),
    }


@app.post("/v1/repo_improvement/delivery/run")
@app.post("/v1/self_upgrade/delivery/run")
def v1_repo_improvement_delivery_run(payload: SelfUpgradeDeliveryIn):
    _require_leader_write()
    out = _run_self_upgrade_delivery_iteration(
        actor="repo_improvement_delivery_api",
        project_id=str(payload.project_id or "teamos").strip() or "teamos",
        target_id=str(payload.target_id or "").strip(),
        task_id=str(payload.task_id or "").strip(),
        dry_run=bool(payload.dry_run),
        force=bool(payload.force),
        max_tasks=payload.max_tasks,
    )
    return out


@app.post("/v1/self_improve/run")
def v1_self_improve_run(payload: SelfUpgradeIn):
    out = v1_repo_improvement_run(payload)
    try:
        emit_n8n_event(
            "REPO_IMPROVEMENT_RUN",
            project_id=str(out.get("project_id") or "teamos"),
            workstream_id=str(payload.workstream_id or _default_workstream_id()),
            payload=out,
        )
    except Exception:
        pass
    return out


@app.get("/v1/events/stream")
def v1_events_stream(after_id: int = 0):
    # SSE stream from the runtime DB events table.
    async def gen():
        last = int(after_id)
        while True:
            rows = DB.list_events(after_id=last, limit=200)
            if rows:
                for e in rows:
                    last = max(last, e.id)
                    payload = {
                        "id": e.id,
                        "ts": e.ts,
                        "event_type": e.event_type,
                        "actor": e.actor,
                        "project_id": e.project_id,
                        "workstream_id": e.workstream_id,
                        "payload": e.payload,
                    }
                    yield f"id: {e.id}\n".encode("utf-8")
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
            import asyncio

            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _default_project_id() -> str:
    return "teamos"


def _default_workstream_id() -> str:
    ws = load_workstreams()
    if ws and ws[0].get("id"):
        return str(ws[0]["id"])
    return "general"


def _append_conversation(project_id: str, payload: dict[str, Any]) -> None:
    d = _conversations_dir(project_id, ensure=True)
    d.mkdir(parents=True, exist_ok=True)
    day = _utc_now_iso().split("T", 1)[0]
    path = d / f"{day}.jsonl"
    item = dict(payload)
    item["ts"] = _utc_now_iso()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _handle_new_requirement(
    *,
    project_id: str,
    workstream_id: str,
    requirement_text: str,
    priority: str = "P2",
    rationale: str = "",
    constraints: Optional[list[str]] = None,
    acceptance: Optional[list[str]] = None,
    source: str = "chat",
) -> dict[str, Any]:
    req_dir = _requirements_dir(project_id, ensure=True)
    ch = str(source or "").strip().lower()
    if ch not in ("cli", "api", "chat", "import"):
        ch = "chat" if source == "chat" else "api"
    try:
        outcome = add_requirement_raw_first(
            project_id=project_id,
            req_dir=req_dir,
            requirement_text=requirement_text,
            workstream_id=workstream_id,
            priority=priority,
            rationale=rationale,
            constraints=constraints,
            acceptance=acceptance,
            source=source,
            channel=ch,
            user="user" if ch == "chat" else ch,
        )
    except (StateError, RequirementsError) as e:
        DB.add_event(
            event_type="REQUIREMENT_ADD_FAILED",
            actor="control-plane",
            project_id=project_id,
            workstream_id=workstream_id,
            payload={"error": str(e)},
        )
        raise

    DB.add_event(
        event_type="REQUIREMENT_SUBMITTED",
        actor="user",
        project_id=project_id,
        workstream_id=workstream_id,
        payload={
            "classification": outcome.classification,
            "req_id": outcome.req_id or "",
            "duplicate_of": outcome.duplicate_of or "",
            "conflicts_with": outcome.conflicts_with,
            "conflict_report_path": outcome.conflict_report_path or "",
            "drift_report_path": outcome.drift_report_path or "",
            "raw_id": outcome.raw_id or "",
            "raw_input_timestamp": outcome.raw_input_timestamp or "",
            "feasibility_outcome": outcome.feasibility_outcome or "",
            "feasibility_report_path": outcome.feasibility_report_path or "",
        },
    )
    _mark_panel_dirty(project_id)
    if outcome.classification in ("CONFLICT", "DRIFT", "NEED_PM_DECISION"):
        try:
            emit_n8n_event(
                "need_pm_decision",
                project_id=project_id,
                workstream_id=workstream_id,
                payload={
                    "req_id": outcome.req_id or "",
                    "conflicts_with": outcome.conflicts_with,
                    "conflict_report_path": outcome.conflict_report_path or "",
                    "drift_report_path": outcome.drift_report_path or "",
                    "feasibility_report_path": outcome.feasibility_report_path or "",
                },
            )
        except Exception:
            pass

    if outcome.classification == "DUPLICATE":
        summary = "\n".join(
            [
                "NEW_REQUIREMENT processed: DUPLICATE",
                f"- duplicate_of={outcome.duplicate_of}",
                "- no changes made to requirements.yaml",
                f"- changelog={req_dir / 'CHANGELOG.md'}",
            ]
        )
    elif outcome.classification == "CONFLICT":
        summary = "\n".join(
            [
                "NEW_REQUIREMENT processed: CONFLICT -> NEED_PM_DECISION",
                f"- req_id={outcome.req_id}",
                f"- conflicts_with={','.join(outcome.conflicts_with)}",
                f"- conflict_report={outcome.conflict_report_path}",
                f"- requirements_yaml={req_dir / 'requirements.yaml'}",
                "Next: resolve pending decision via PM (choose A/B/C in the conflict report).",
            ]
        )
    elif outcome.classification == "DRIFT":
        summary = "\n".join(
            [
                "NEW_REQUIREMENT blocked: DRIFT detected -> NEED_PM_DECISION",
                f"- raw_id={outcome.raw_id}",
                f"- raw_input_ts={outcome.raw_input_timestamp}",
                f"- drift_report={outcome.drift_report_path}",
                f"- requirements_yaml={req_dir / 'requirements.yaml'}",
                "Next: fix drift first (see DRIFT report options A/B/C).",
            ]
        )
    elif outcome.classification == "NEED_PM_DECISION":
        summary = "\n".join(
            [
                "NEW_REQUIREMENT blocked: FEASIBILITY -> NEED_PM_DECISION",
                f"- raw_id={outcome.raw_id}",
                f"- raw_input_ts={outcome.raw_input_timestamp}",
                f"- feasibility_outcome={outcome.feasibility_outcome}",
                f"- feasibility_report={outcome.feasibility_report_path}",
                f"- requirements_yaml={req_dir / 'requirements.yaml'}",
                "Next: provide missing info / resolve feasibility blockers, then re-submit.",
            ]
        )
    else:
        summary = "\n".join(
            [
                "NEW_REQUIREMENT processed: COMPATIBLE",
                f"- req_id={outcome.req_id}",
                f"- requirements_yaml={req_dir / 'requirements.yaml'}",
                f"- requirements_md={req_dir / 'REQUIREMENTS.md'}",
            ]
        )

    return {
        "summary": summary,
        "classification": outcome.classification,
        "req_id": outcome.req_id,
        "duplicate_of": outcome.duplicate_of,
        "conflicts_with": outcome.conflicts_with,
        "conflict_report_path": outcome.conflict_report_path,
        "raw_id": outcome.raw_id,
        "feasibility_outcome": outcome.feasibility_outcome,
        "feasibility_report_path": outcome.feasibility_report_path,
        "actions_taken": outcome.actions_taken,
        "pending_decisions": outcome.pending_decisions,
    }


def _load_tasks_summary() -> list[dict[str, Any]]:
    # Team OS self tasks live in-repo. All other project tasks must live in Workspace.
    scan: list[tuple[str, Path]] = [("teamos", ledger_tasks_dir())]
    for pid in _list_workspace_projects():
        if pid == "teamos":
            continue
        scan.append((pid, workspace_store.ledger_tasks_dir(pid)))

    out: list[dict[str, Any]] = []
    for pid, d in scan:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.yaml")):
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            tid = str(data.get("id") or p.stem)
            title = str(data.get("title") or "")
            state = str(data.get("status") or data.get("state") or "")
            owners = data.get("owners") or []
            owner_role = str(owners[0]) if owners else ""
            workstream_id = str(data.get("workstream_id") or "general")
            project_id = str(data.get("project_id") or pid or _default_project_id())
            risk = str(data.get("risk_level") or data.get("risk") or "")
            need_pm = bool(data.get("need_pm_decision") or False)
            execution = data.get("self_upgrade_execution") or {}

            out.append(
                {
                    "task_id": tid,
                    "title": title,
                    "state": state,
                    "owner_role": owner_role,
                    "workstream_id": workstream_id,
                    "project_id": project_id,
                    "risk": risk,
                    "need_pm_decision": need_pm,
                    "links": data.get("links") or {},
                    "ledger_path": str(p),
                    "orchestration": data.get("orchestration") or {},
                    "self_upgrade_stage": str((execution if isinstance(execution, dict) else {}).get("stage") or ""),
                }
            )
    return out


def _pending_decisions() -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []

    # 1) Requirement conflicts per project.
    for pid in _all_project_ids():
        try:
            req_dir = _requirements_dir(pid, ensure=False)
            y = req_dir / "requirements.yaml"
            if not y.exists():
                continue
            data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
            for r in data.get("requirements") or []:
                st = str(r.get("status") or "").upper()
                if st == "NEED_PM_DECISION":
                    decisions.append(
                        {
                            "type": "REQUIREMENT_NEED_PM_DECISION",
                            "project_id": pid,
                            "req_id": r.get("req_id"),
                            "title": r.get("title"),
                            "conflicts_with": r.get("conflicts_with") or [],
                            "decision_log_refs": r.get("decision_log_refs") or [],
                        }
                    )
        except Exception:
            continue

    # 2) Tasks flagged need_pm_decision.
    for t in _load_tasks_summary():
        if t.get("need_pm_decision"):
            decisions.append({"type": "TASK_NEED_PM_DECISION", "task_id": t.get("task_id"), "title": t.get("title")})

    # 3) Self-upgrade proposals waiting for user input.
    try:
        for p in crewai_self_upgrade.list_proposals():
            status = str(p.get("status") or "").strip().upper()
            lane = str(p.get("lane") or "").strip().lower()
            if lane not in ("feature", "quality") or status not in ("PENDING_CONFIRMATION", "HOLD"):
                continue
            decisions.append(
                {
                    "type": "REPO_IMPROVEMENT_PROPOSAL_DECISION",
                    "project_id": str(p.get("project_id") or "teamos"),
                    "proposal_id": p.get("proposal_id"),
                    "title": p.get("title"),
                    "lane": lane,
                    "status": status,
                    "target_version": p.get("target_version"),
                    "cooldown_until": p.get("cooldown_until"),
                }
            )
    except Exception:
        pass

    return decisions
