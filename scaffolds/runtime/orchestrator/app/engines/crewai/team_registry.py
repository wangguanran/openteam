from __future__ import annotations

from dataclasses import dataclass

from app import crewai_spec_loader


@dataclass(frozen=True)
class TeamSpec:
    team_id: str
    display_name_zh: str = ""
    mission: str = ""
    role_pool: tuple[str, ...] = ()
    workflow_ids: tuple[str, ...] = ()
    stage_ids: tuple[str, ...] = ()


def _normalize_items(raw: object) -> tuple[str, ...]:
    out: list[str] = []
    for item in list(raw or []):  # type: ignore[arg-type]
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _team_spec_from_doc(doc: dict[str, object]) -> TeamSpec:
    return TeamSpec(
        team_id=str(doc.get("team_id") or "").strip(),
        display_name_zh=str(doc.get("display_name_zh") or "").strip(),
        mission=str(doc.get("mission") or "").strip(),
        role_pool=_normalize_items(doc.get("role_pool")),
        workflow_ids=_normalize_items(doc.get("workflow_ids")),
        stage_ids=_normalize_items(doc.get("stage_ids")),
    )


def list_teams() -> tuple[TeamSpec, ...]:
    docs = crewai_spec_loader.list_spec_docs("teams")
    specs: list[TeamSpec] = []
    for doc in docs:
        spec = _team_spec_from_doc(dict(doc))
        if spec.team_id:
            specs.append(spec)
    return tuple(specs)


def team_spec(team_id: str) -> TeamSpec:
    doc = crewai_spec_loader.team_doc(str(team_id or "").strip())
    if not doc:
        raise KeyError(f"unknown team spec: {team_id}")
    spec = _team_spec_from_doc(doc)
    if not spec.team_id:
        raise KeyError(f"invalid team spec: {team_id}")
    return spec


def default_team_id() -> str:
    teams = list_teams()
    if not teams:
        raise KeyError("no team specs configured")
    return teams[0].team_id
