from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable

from app import team_registry
from app import improvement_store
from app import team_workflow_runtime


@dataclass(frozen=True)
class TeamRuntimeAdapter:
    team_id: str
    run_once_fn: Callable[..., dict[str, Any]]
    read_state_fn: Callable[..., dict[str, Any]]
    list_proposals_fn: Callable[..., list[dict[str, Any]]]
    decide_proposal_fn: Callable[..., dict[str, Any]]
    reconcile_discussions_fn: Callable[..., dict[str, Any]]
    list_delivery_tasks_fn: Callable[..., list[dict[str, Any]]]
    delivery_summary_fn: Callable[..., dict[str, Any]]
    run_delivery_sweep_fn: Callable[..., dict[str, Any]]
    migrate_legacy_worktrees_fn: Callable[..., dict[str, Any]]


def _unsupported(*args: Any, **kwargs: Any) -> Any:
    _ = args, kwargs
    raise RuntimeError("team_runtime_operation_unsupported")


def _empty_state(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _ = args, kwargs
    return {}


def _empty_items(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _ = args, kwargs
    return []


def _empty_summary(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _ = args, kwargs
    return {"total": 0}


def _generic_team_list_proposals(*, team_id: str, target_id: str = "", project_id: str = "", lane: str = "", status: str = "") -> list[dict[str, Any]]:
    return improvement_store.list_proposals(
        team_id=str(team_id or "").strip(),
        target_id=str(target_id or "").strip(),
        project_id=str(project_id or "").strip(),
        lane=str(lane or "").strip(),
        status=str(status or "").strip(),
    )


def _generic_team_decide_proposal(*, team_id: str, proposal_id: str, action: str, title: str = "", summary: str = "", version_bump: str = "") -> dict[str, Any]:
    _ = version_bump
    pid = str(proposal_id or "").strip()
    if not pid:
        raise RuntimeError("proposal_id is required")
    act = str(action or "").strip().lower()
    if act not in {"approve", "reject", "hold"}:
        raise RuntimeError("action must be one of: approve, reject, hold")
    doc = improvement_store.get_proposal(pid)
    if not isinstance(doc, dict):
        raise RuntimeError(f"proposal not found: {pid}")
    if str(doc.get("team_id") or "").strip() != str(team_id or "").strip():
        raise RuntimeError(f"proposal_team_mismatch: {pid}")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if title:
        doc["title"] = str(title).strip()
    if summary:
        doc["summary"] = str(summary).strip()
    if act == "approve":
        doc["status"] = "APPROVED"
        doc["approved_at"] = now
    elif act == "reject":
        doc["status"] = "REJECTED"
        doc["rejected_at"] = now
    else:
        doc["status"] = "HOLD"
    doc["updated_at"] = now
    improvement_store.upsert_proposal(doc)
    return {"proposal_id": pid, **doc}


def _generic_team_list_delivery_tasks(*, team_id: str, project_id: str = "", target_id: str = "", status: str = "") -> list[dict[str, Any]]:
    return improvement_store.list_delivery_tasks(
        team_id=str(team_id or "").strip(),
        project_id=str(project_id or "").strip(),
        target_id=str(target_id or "").strip(),
        status=str(status or "").strip(),
    )


def _generic_team_delivery_summary(*, team_id: str, project_id: str = "", target_id: str = "") -> dict[str, Any]:
    tasks = _generic_team_list_delivery_tasks(team_id=team_id, project_id=project_id, target_id=target_id)
    summary = {
        "total": len(tasks),
        "queued": 0,
        "coding": 0,
        "blocked": 0,
        "closed": 0,
    }
    for task in tasks:
        status = str(task.get("status") or task.get("state") or "").strip().lower()
        if status in {"todo", "queued", "pending"}:
            summary["queued"] += 1
        elif status in {"doing", "running", "work", "coding", "in_progress", "inprogress"}:
            summary["coding"] += 1
        elif status in {"blocked", "hold"}:
            summary["blocked"] += 1
        elif status in {"closed", "done"}:
            summary["closed"] += 1
        else:
            summary["queued"] += 1
    return summary


def team_runtime_adapter(team_id: str) -> TeamRuntimeAdapter:
    wanted = str(team_id or "").strip()
    if not wanted:
        raise KeyError("team_id is required")

    return TeamRuntimeAdapter(
        team_id=wanted,
        run_once_fn=lambda **kwargs: team_workflow_runtime.run_team_iteration(team_id=wanted, **kwargs),
        read_state_fn=team_workflow_runtime.read_state,
        list_proposals_fn=lambda **kwargs: team_workflow_runtime.list_proposals(team_id=wanted, **kwargs),
        decide_proposal_fn=lambda **kwargs: team_workflow_runtime.decide_proposal(team_id=wanted, **kwargs),
        reconcile_discussions_fn=lambda **kwargs: team_workflow_runtime.reconcile_discussions(team_id=wanted, **kwargs),
        list_delivery_tasks_fn=lambda **kwargs: team_workflow_runtime.list_delivery_tasks(team_id=wanted, **kwargs),
        delivery_summary_fn=lambda **kwargs: team_workflow_runtime.delivery_summary(team_id=wanted, **kwargs),
        run_delivery_sweep_fn=lambda **kwargs: team_workflow_runtime.run_delivery_sweep(team_id=wanted, **kwargs),
        migrate_legacy_worktrees_fn=team_workflow_runtime.migrate_legacy_worktrees,
    )


def default_team_runtime_adapter() -> TeamRuntimeAdapter:
    return team_runtime_adapter(team_registry.default_team_id())


def _generic_team_run_once(*, db: Any, spec: Any, actor: str, run_id: str, crewai_info: dict[str, Any]) -> dict[str, Any]:
    flow = str(getattr(spec, "flow", "") or "").strip()
    if not flow.startswith("team:"):
        raise RuntimeError(f"unsupported_generic_team_flow: {flow}")
    team_id = flow.split(":", 1)[1].strip()
    return team_workflow_runtime.run_team_iteration(
        team_id=team_id,
        db=db,
        spec=spec,
        actor=actor,
        run_id=run_id,
        crewai_info=crewai_info,
    )
