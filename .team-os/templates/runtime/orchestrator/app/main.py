import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from agents import Agent  # OpenAI Agents SDK (placeholder; must not call models on startup)
from fastapi import FastAPI, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import codex_llm
from .demo_seed import seed_mock_data
from .requirements_store import RequirementsError, add_requirement
from .runtime_db import RuntimeDB
from .state_store import (
    StateError,
    conversations_dir,
    ensure_instance_id,
    ledger_tasks_dir,
    load_focus,
    load_projects,
    load_workstreams,
    requirements_dir_for_project,
    save_focus,
    team_os_root,
)


app = FastAPI(title="Team OS Control Plane", version="0.2.0")


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


def _seed_if_enabled() -> None:
    if os.getenv("TEAMOS_DEMO_SEED", "").strip() in ("1", "true", "TRUE", "yes", "YES"):
        # Seed against first project if present.
        projects = load_projects()
        if projects:
            pid = str(projects[0].get("project_id") or "DEMO")
        else:
            pid = "DEMO"
        seed_mock_data(DB, project_id=pid, workstream_id="ai")


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


@app.get("/healthz")
def healthz(response: Response):
    team_os_path = os.getenv("TEAM_OS_REPO_PATH", "/team-os")
    checks = _team_os_checks(team_os_path)
    ok = checks["exists"] and checks["workflows_dir_exists"] and checks["roles_dir_exists"]
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks}


@app.get("/v1/status")
def v1_status():
    instance_id = ensure_instance_id()
    focus = load_focus()

    projects = load_projects()
    active_projects = []
    for p in projects:
        active_projects.append(
            {
                "project_id": p.get("project_id"),
                "name": p.get("name"),
                "workstreams": p.get("workstreams") or [],
            }
        )

    runs = [r.__dict__ for r in DB.list_runs()]
    agents = [a.__dict__ for a in DB.list_agents()]

    tasks = _load_tasks_summary()

    pending = _pending_decisions()

    return {
        "instance_id": instance_id,
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
    return f


@app.get("/v1/auth/status")
def v1_auth_status():
    try:
        ok, msg = codex_llm.codex_login_status()
        return {"backend": "codex", "logged_in": ok, "message": msg}
    except codex_llm.CodexUnavailable as e:
        return {"backend": "codex", "logged_in": False, "message": str(e)}


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
        actions.append(f"run_state={desired}")
        response_lines.append(f"run_id={payload.run_id} state={desired}")
        return {"response_text": "\n".join(response_lines).strip() + "\n", "actions_taken": actions, "pending_decisions": pending}

    if msg_type == "NEW_REQUIREMENT":
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
        response_lines.append(
            "\n".join(
                [
                    "Message recorded.",
                    f"- project_id={project_id} workstream_id={workstream_id} message_type={msg_type}",
                    f"- conversation_log=.team-os/ledger/conversations/{project_id}/<YYYY-MM-DD>.jsonl",
                    "Tip: use `/req <text>` in CLI or set message_type=NEW_REQUIREMENT to register requirements with conflict check.",
                ]
            )
        )

    return {"response_text": "\n".join(response_lines).strip() + "\n", "actions_taken": actions, "pending_decisions": pending}


@app.post("/v1/requirements")
def v1_requirements_add(payload: RequirementIn):
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
    req_dir = requirements_dir_for_project(project_id)
    y = req_dir / "requirements.yaml"
    if not y.exists():
        return {"project_id": project_id, "requirements": [], "conflicts_dir": str(req_dir / "conflicts")}
    with y.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {
        "project_id": project_id,
        "requirements_dir": str(req_dir),
        "requirements": data.get("requirements") or [],
    }


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
    projects = load_projects()
    if projects and projects[0].get("project_id"):
        return str(projects[0]["project_id"])
    return "DEMO"


def _default_workstream_id() -> str:
    ws = load_workstreams()
    if ws and ws[0].get("id"):
        return str(ws[0]["id"])
    return "general"


def _append_conversation(project_id: str, payload: dict[str, Any]) -> None:
    d = conversations_dir(project_id)
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
    req_dir = requirements_dir_for_project(project_id)
    try:
        outcome = add_requirement(
            project_id=project_id,
            req_dir=req_dir,
            requirement_text=requirement_text,
            priority=priority,
            rationale=rationale,
            constraints=constraints,
            acceptance=acceptance,
            source=source,
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
        },
    )

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
    d = ledger_tasks_dir()
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
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
        project_id = str(data.get("project_id") or _default_project_id())
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
    for p in load_projects():
        pid = str(p.get("project_id") or "")
        if not pid:
            continue
        try:
            req_dir = requirements_dir_for_project(pid)
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
