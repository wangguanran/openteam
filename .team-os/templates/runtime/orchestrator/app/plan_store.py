from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from .state_store import plan_dir_for_project


class PlanError(Exception):
    pass


@dataclass(frozen=True)
class Milestone:
    milestone_id: str
    title: str
    start_date: str  # YYYY-MM-DD
    target_date: str  # YYYY-MM-DD
    workstreams: list[str]
    objective: str
    links: list[str]


def load_plan_yaml(project_id: str) -> Optional[dict[str, Any]]:
    d = plan_dir_for_project(project_id)
    if not d:
        return None
    y = d / "plan.yaml"
    if not y.exists():
        return None
    try:
        data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise PlanError(f"invalid plan.yaml: {y}: {e}") from e
    return data


def list_milestones(project_id: str) -> list[Milestone]:
    data = load_plan_yaml(project_id)
    if not data:
        return []
    out: list[Milestone] = []
    for m in (data.get("milestones") or []):
        try:
            out.append(
                Milestone(
                    milestone_id=str(m.get("milestone_id") or "").strip(),
                    title=str(m.get("title") or "").strip(),
                    start_date=str(m.get("start_date") or "").strip(),
                    target_date=str(m.get("target_date") or "").strip(),
                    workstreams=list(m.get("workstreams") or []),
                    objective=str(m.get("objective") or "").strip(),
                    links=[str(x) for x in (m.get("links") or [])],
                )
            )
        except Exception:
            continue
    return [m for m in out if m.milestone_id and m.title]

