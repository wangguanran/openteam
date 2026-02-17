import json
import os
import subprocess
import sys
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
from .n8n_hook import emit_n8n_event
from .panel_github_sync import GitHubProjectsPanelSync, PanelSyncError
from .panel_mapping import PanelMappingError, load_mapping
from .requirements_store import (
    RequirementsError,
    add_requirement_raw_first,
    rebuild_requirements_md,
    verify_requirements_raw_first,
    propose_baseline_v2,
)
from .runtime_db import RuntimeDB
from .self_improve_runner import SelfImproveError
from . import self_improve_runner
from . import cluster_manager
from .state_store import (
    StateError,
    ensure_instance_id,
    ledger_tasks_dir,
    logs_tasks_dir,
    load_focus,
    load_workstreams,
    github_projects_mapping_path,
    save_focus,
    team_os_root,
    teamos_requirements_dir,
)
from . import workspace_store


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


def _team_os_checks(team_os_path: str) -> dict[str, Any]:
    p = Path(team_os_path)
    workflows_dir = p / ".team-os" / "workflows"
    roles_dir = p / ".team-os" / "roles"
    state_dir = p / ".team-os" / "state"
    return {
        "team_os_path": str(p),
        "exists": p.exists(),
        "workflows_dir_exists": workflows_dir.exists(),
        "roles_dir_exists": roles_dir.exists(),
        "state_dir_exists": state_dir.exists(),
        "workflow_files": sorted([x.name for x in workflows_dir.glob("*.yaml")]) if workflows_dir.exists() else [],
        "role_files": sorted([x.name for x in roles_dir.glob("*.md")]) if roles_dir.exists() else [],
    }


def _db() -> RuntimeDB:
    db_path = os.getenv("RUNTIME_DB_PATH")
    if not db_path:
        db_path = str(team_os_root() / ".team-os" / "state" / "runtime.db")
    return RuntimeDB(db_path)


DB = _db()

# --- Panel sync scheduling (best-effort; GitHub Projects is view-layer) ---
_PANEL_DIRTY: set[str] = set()
_PANEL_LOCK = threading.Lock()


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


def _plan_dir(project_id: str, *, ensure: bool) -> Path:
    if _is_teamos(project_id):
        # teamos plan stays in-repo (scope=teamos).
        return team_os_root() / "docs" / "plan" / "teamos"
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
        return team_os_root() / ".team-os" / "ledger" / "conversations" / "teamos"
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


def _env_truthy(name: str, default: str = "1") -> bool:
    v = os.getenv(name, default).strip().lower()
    return v not in ("0", "false", "no", "off", "")


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

    # Always-on self-improve: ensure the host-level daemon is running.
    def _ensure_self_improve_daemon() -> None:
        if str(os.getenv("TEAMOS_SELF_IMPROVE_AUTO_START", "1") or "").strip().lower() in ("0", "false", "no", "off"):
            return
        try:
            time.sleep(3)
            repo = team_os_root()
            script = repo / ".team-os" / "scripts" / "pipelines" / "self_improve_daemon.py"
            if not script.exists():
                return
            try:
                ws = str(_workspace_root())
            except Exception:
                ws = str(Path.home() / ".teamos" / "workspace")
            argv = [sys.executable, str(script), "--repo-root", str(repo), "--workspace-root", ws, "start"]
            subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            # Never crash server because of daemon management.
            pass

    st = threading.Thread(target=_ensure_self_improve_daemon, name="self-improve-daemon-ensure", daemon=True)
    st.start()


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


class SelfImproveIn(BaseModel):
    dry_run: bool = True
    force: bool = False
    trigger: str = "api"  # api|cli_auto|manual


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
    ok = checks["exists"] and checks["workflows_dir_exists"] and checks["roles_dir_exists"]
    db = {"backend": ("postgres" if (os.getenv("TEAMOS_DB_URL") or "").strip() else "sqlite"), "ok": True, "error": ""}
    try:
        # Minimal DB probe (no side effects).
        _ = DB.list_events(after_id=0, limit=1)
    except Exception as e:
        db["ok"] = False
        db["error"] = str(e)[:200]
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks, "db": db}


@app.get("/v1/status")
def v1_status():
    instance_id = ensure_instance_id()
    focus = load_focus()
    ws_root = str(_workspace_root())
    active_projects = _active_projects_summary()

    runs = [r.__dict__ for r in DB.list_runs()]
    agents = [a.__dict__ for a in DB.list_agents()]

    tasks = _load_tasks_summary()

    pending = _pending_decisions()

    return {
        "instance_id": instance_id,
        "workspace_root": ws_root,
        "workspace_projects_count": len(_list_workspace_projects()),
        "current_focus": focus,
        "active_projects": active_projects,
        "active_runs": runs,
        "agents": agents,
        "tasks": tasks,
        "pending_decisions": pending,
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
        # Optional notification hook (n8n): treat run state updates as a "task_state_changed" signal.
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


def _render_log_template(tpl_path: Path, *, task_id: str, title: str) -> str:
    text = tpl_path.read_text(encoding="utf-8")
    date = _utc_now_iso().split("T", 1)[0]
    return text.replace("{{TASK_ID}}", task_id).replace("{{TITLE}}", title).replace("{{DATE}}", date)


def _create_task_scaffold(*, title: str, project_id: str, workstream_id: str, mode: str) -> dict[str, Any]:
    # Task truth source:
    # - scope=teamos -> in-repo `.team-os/ledger` + `.team-os/logs`
    # - scope=project:<id> -> workspace `projects/<id>/state/...`
    #
    # Templates are always sourced from the Team OS repo.
    root = team_os_root()
    tasks_dir = _ledger_tasks_dir(project_id, ensure=True)
    logs_root = _logs_tasks_dir(project_id, ensure=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)

    # Task id
    task_id = ""
    for _ in range(10):
        cand = f"TASK-{_ts_compact_utc()}"
        if not (tasks_dir / f"{cand}.yaml").exists():
            task_id = cand
            break
        time.sleep(1)
    if not task_id:
        raise RuntimeError("failed to generate task_id")

    now = _utc_now_iso()
    in_repo = _is_teamos(project_id)
    artifacts_ledger = (f".team-os/ledger/tasks/{task_id}.yaml") if in_repo else (f"state/ledger/tasks/{task_id}.yaml")
    artifacts_logs_dir = (f".team-os/logs/tasks/{task_id}/") if in_repo else (f"state/logs/tasks/{task_id}/")
    evidence_intake = (f".team-os/logs/tasks/{task_id}/00_intake.md") if in_repo else (f"state/logs/tasks/{task_id}/00_intake.md")
    ledger = {
        "id": task_id,
        "title": title,
        "project_id": project_id,
        "workstream_id": workstream_id,
        "status": "intake",
        "risk_level": "R1",
        "need_pm_decision": False,
        "repo": {"locator": "", "workdir": "", "branch": "", "mode": mode},
        "checkpoint": {"stage": "intake", "last_event_ts": now},
        "recovery": {"last_scan_at": "", "last_resume_at": "", "notes": ""},
        "owners": ["PM-Intake"],
        "roles_involved": ["PM-Intake"],
        "workflows": ["Genesis"],
        "created_at": now,
        "updated_at": now,
        "links": {"pr": "", "issue": ""},
        "artifacts": {"ledger": artifacts_ledger, "logs_dir": artifacts_logs_dir},
        "evidence": [{"type": "log", "path": evidence_intake}],
    }
    ledger_path = tasks_dir / f"{task_id}.yaml"
    ledger_path.write_text(yaml.safe_dump(ledger, sort_keys=False, allow_unicode=True), encoding="utf-8")

    logs_dir = logs_root / task_id
    logs_dir.mkdir(parents=True, exist_ok=True)
    tpls = root / ".team-os" / "templates"
    for name, tpl in [
        ("00_intake.md", "task_log_00_intake.md"),
        ("01_plan.md", "task_log_01_plan.md"),
        ("02_todo.md", "task_log_02_todo.md"),
        ("03_work.md", "task_log_03_work.md"),
        ("04_test.md", "task_log_04_test.md"),
        ("05_release.md", "task_log_05_release.md"),
        ("06_observe.md", "task_log_06_observe.md"),
        ("07_retro.md", "task_log_07_retro.md"),
    ]:
        out = logs_dir / name
        if out.exists():
            continue
        tp = tpls / tpl
        if not tp.exists():
            continue
        out.write_text(_render_log_template(tp, task_id=task_id, title=title), encoding="utf-8")

    metrics = logs_dir / "metrics.jsonl"
    if not metrics.exists():
        metrics.write_text(
            json.dumps(
                {
                    "ts": now,
                    "event_type": "TASK_CREATED",
                    "actor": "control-plane",
                    "task_id": task_id,
                    "project_id": project_id,
                    "workstream_id": workstream_id,
                    "severity": "INFO",
                    "message": "task scaffold created",
                    "payload": {"ledger": str(ledger_path), "logs_dir": str(logs_dir)},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    return {"task_id": task_id, "ledger_path": str(ledger_path), "logs_dir": str(logs_dir)}


@app.post("/v1/tasks/new")
def v1_tasks_new(payload: TaskNewIn):
    # Safety: do not touch remotes by default.
    if payload.create_repo_if_missing and not (payload.repo_locator or "").strip():
        # Repo creation is high risk -> require explicit approval and remote-write enable.
        raise HTTPException(status_code=412, detail="Repo creation requires explicit approval (high risk). Provide repo_locator or enable approved flow.")

    mode = (payload.mode or "auto").strip().lower()
    if mode not in ("auto", "bootstrap", "upgrade"):
        mode = "auto"

    wsid = str((payload.workstreams or ["general"])[0] if payload.workstreams else "general")
    created = _create_task_scaffold(title=payload.title, project_id=payload.project_id, workstream_id=wsid, mode=mode)
    DB.add_event(
        event_type="TASK_NEW",
        actor="user",
        project_id=payload.project_id,
        workstream_id=wsid,
        payload={"task_id": created["task_id"], "mode": mode, "dry_run": bool(payload.dry_run), "repo_locator": (payload.repo_locator or "")[:120]},
    )
    _mark_panel_dirty(payload.project_id)
    pending: list[dict[str, Any]] = []
    if mode == "upgrade":
        pending.append(
            {
                "type": "REPO_UNDERSTANDING_GATE",
                "project_id": payload.project_id,
                "task_id": created["task_id"],
                "message": "mode=upgrade requires docs/team_os/REPO_UNDERSTANDING.md before any code changes.",
                "artifact_template": ".team-os/templates/repo_understanding.md",
            }
        )
    return {"task_id": created["task_id"], "ledger_path": created["ledger_path"], "logs_dir": created["logs_dir"], "pending_decisions": pending}


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
    snap_dir = team_os_root() / ".team-os" / "cluster" / "state"
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
    skipped: list[dict[str, Any]] = []
    for t in target:
        gates = _gates_for_task(t)
        if gates:
            skipped.append({"task_id": t.get("task_id"), "gates": gates})
            continue

        run_id = f"run-{t.get('task_id')}"
        DB.upsert_run(run_id=run_id, project_id=str(t.get("project_id") or ""), workstream_id=str(t.get("workstream_id") or ""), objective=str(t.get("title") or ""), state="RUNNING")

        # Ensure a single Process-Guardian placeholder agent per task (best-effort; avoid duplicates).
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

    DB.add_event(event_type="RECOVERY_RESUME", actor="control-plane", project_id=_default_project_id(), workstream_id=_default_workstream_id(), payload={"resumed": resumed, "skipped": skipped})
    _mark_panel_dirty()
    return {"ok": True, "resumed": resumed, "skipped": skipped}


@app.post("/v1/self_improve/run")
def v1_self_improve_run(payload: SelfImproveIn):
    """
    Run one self-improve iteration.

    Notes:
    - Remote writes (GitHub Issues/Projects/repo creation) remain gated elsewhere.
    - This endpoint performs local scanning + local truth-source updates (requirements/pending drafts).
    """
    try:
        routes = [getattr(r, "path", "") for r in app.routes if getattr(r, "path", "")]
        out = self_improve_runner.run(
            dry_run=bool(payload.dry_run),
            force=bool(payload.force),
            actor="user",
            trigger=str(payload.trigger or "api"),
            api_routes=routes,
            project_id="teamos",
        )
        # Best-effort: compute a panel sync plan (dry-run) so the user can see what would appear on the Projects panel.
        try:
            svc = GitHubProjectsPanelSync(db=DB)
            out["panel_sync_dry_run"] = svc.sync(project_id="teamos", mode="incremental", dry_run=True)
        except Exception as e:
            out["panel_sync_dry_run_error"] = str(e)[:200]
        DB.add_event(
            event_type="SELF_IMPROVE_RUN",
            actor="user",
            project_id="teamos",
            workstream_id=_default_workstream_id(),
            payload={"dry_run": bool(payload.dry_run), "force": bool(payload.force), "trigger": str(payload.trigger or "api"), "skipped": bool(out.get("skipped"))},
        )
        _mark_panel_dirty("teamos")
        return out
    except Exception as e:
        DB.add_event(
            event_type="SELF_IMPROVE_RUN_FAILED",
            actor="user",
            project_id="teamos",
            workstream_id=_default_workstream_id(),
            payload={"error": str(e)[:500], "dry_run": bool(payload.dry_run), "force": bool(payload.force)},
        )
        raise HTTPException(status_code=500, detail=str(e)[:500])


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
            "raw_input_timestamp": outcome.raw_input_timestamp or "",
        },
    )
    _mark_panel_dirty(project_id)

    if outcome.classification in ("CONFLICT", "DRIFT", "NEED_PM_DECISION"):
        # Optional notification hook (n8n): pending decision created.
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
                f"- raw_input_ts={outcome.raw_input_timestamp}",
                f"- drift_report={outcome.drift_report_path}",
                f"- requirements_yaml={req_dir / 'requirements.yaml'}",
                "Next: fix drift first (see DRIFT report options A/B/C).",
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

    return decisions
