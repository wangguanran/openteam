from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import improvement_store
from .state_store import runtime_state_root, openteam_plan_dir
from .workspace_store import ensure_project_scaffold, plan_dir, project_state_dir


class PlanError(Exception):
    pass


@dataclass(frozen=True)
class Milestone:
    milestone_id: str
    title: str
    target_id: str = ""
    start_date: str = ""  # YYYY-MM-DD
    target_date: str = ""  # YYYY-MM-DD
    workstreams: list[str] = field(default_factory=list)
    objective: str = ""
    links: list[str] = field(default_factory=list)
    state: str = "draft"
    release_line: str = ""
    target_version: str = ""
    version_bump: str = ""
    repo_locator: str = ""
    manager_role: str = ""
    github_milestone_number: int = 0
    release_issue_number: int = 0
    release_issue_url: str = ""
    total_items: int = 0
    open_items: int = 0
    blocked_items: int = 0
    done_items: int = 0
    updated_at: str = ""


def load_plan_yaml(project_id: str) -> Optional[dict[str, Any]]:
    if str(project_id) == "openteam":
        d = openteam_plan_dir()
    else:
        # Ensure project scaffold exists so plan dir can be created lazily.
        ensure_project_scaffold(project_id)
        d = plan_dir(project_id)
    y = d / "plan.yaml"
    if not y.exists():
        return None
    try:
        data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise PlanError(f"invalid plan.yaml: {y}: {e}") from e
    return data


def _runtime_milestones_path(project_id: str) -> Path:
    if str(project_id) == "openteam":
        return runtime_state_root() / "team_workflow_milestones.yaml"
    ensure_project_scaffold(project_id)
    return project_state_dir(project_id) / "plan" / "team_workflow_milestones.yaml"


def _load_runtime_milestones_yaml(project_id: str) -> dict[str, Any]:
    return {"schema_version": 1, "project_id": str(project_id), "milestones": improvement_store.list_milestones(project_id=str(project_id))}


def _write_runtime_milestones_yaml(project_id: str, payload: dict[str, Any]) -> None:
    for raw in list(payload.get("milestones") or []):
        if isinstance(raw, dict):
            improvement_store.upsert_milestone({"project_id": str(project_id), **raw})


def _coerce_milestone(raw: dict[str, Any]) -> Optional[Milestone]:
    try:
        return Milestone(
            milestone_id=str(raw.get("milestone_id") or "").strip(),
            title=str(raw.get("title") or "").strip(),
            target_id=str(raw.get("target_id") or "").strip(),
            start_date=str(raw.get("start_date") or "").strip(),
            target_date=str(raw.get("target_date") or "").strip(),
            workstreams=[str(x).strip() for x in (raw.get("workstreams") or []) if str(x).strip()],
            objective=str(raw.get("objective") or "").strip(),
            links=[str(x).strip() for x in (raw.get("links") or []) if str(x).strip()],
            state=str(raw.get("state") or "draft").strip() or "draft",
            release_line=str(raw.get("release_line") or "").strip(),
            target_version=str(raw.get("target_version") or "").strip(),
            version_bump=str(raw.get("version_bump") or "").strip(),
            repo_locator=str(raw.get("repo_locator") or "").strip(),
            manager_role=str(raw.get("manager_role") or "").strip(),
            github_milestone_number=int(raw.get("github_milestone_number") or 0),
            release_issue_number=int(raw.get("release_issue_number") or 0),
            release_issue_url=str(raw.get("release_issue_url") or "").strip(),
            total_items=int(raw.get("total_items") or 0),
            open_items=int(raw.get("open_items") or 0),
            blocked_items=int(raw.get("blocked_items") or 0),
            done_items=int(raw.get("done_items") or 0),
            updated_at=str(raw.get("updated_at") or "").strip(),
        )
    except Exception:
        return None


def list_milestones(project_id: str) -> list[Milestone]:
    merged: dict[str, Milestone] = {}
    data = load_plan_yaml(project_id) or {}
    for raw in (data.get("milestones") or []):
        if not isinstance(raw, dict):
            continue
        milestone = _coerce_milestone(raw)
        if milestone and milestone.milestone_id and milestone.title:
            merged[milestone.milestone_id] = milestone
    runtime_data = _load_runtime_milestones_yaml(project_id)
    for raw in (runtime_data.get("milestones") or []):
        if not isinstance(raw, dict):
            continue
        milestone = _coerce_milestone(raw)
        if milestone and milestone.milestone_id and milestone.title:
            merged[milestone.milestone_id] = milestone
    return sorted(
        merged.values(),
        key=lambda m: (
            str(m.target_date or "9999-12-31"),
            str(m.start_date or ""),
            str(m.title or m.milestone_id),
        ),
    )


def upsert_runtime_milestone(project_id: str, milestone: dict[str, Any]) -> Milestone:
    milestone_id = str(milestone.get("milestone_id") or "").strip()
    title = str(milestone.get("title") or "").strip()
    if not milestone_id or not title:
        raise PlanError("milestone_id and title are required")
    existing = next((x for x in improvement_store.list_milestones(project_id=str(project_id)) if str(x.get("milestone_id") or "").strip() == milestone_id), {})
    payload = dict(existing or {})
    payload.update(dict(milestone))
    payload["project_id"] = str(project_id)
    improvement_store.upsert_milestone(payload)
    out = _coerce_milestone(payload)
    if out is None:
        raise PlanError(f"invalid milestone payload for milestone_id={milestone_id}")
    return out
