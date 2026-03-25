from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app import spec_loader


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    handler_id: str
    display_name_zh: str = ""
    description: str = ""
    category: str = ""
    idempotent: bool = False
    supports_dry_run: bool = False
    inputs_schema: tuple[tuple[str, Any], ...] = ()
    outputs_schema: tuple[tuple[str, Any], ...] = ()
    tags: tuple[str, ...] = ()


def _normalize_items(raw: Any) -> tuple[tuple[str, Any], ...]:
    if not isinstance(raw, dict):
        return ()
    return tuple((str(key).strip(), value) for key, value in raw.items() if str(key).strip())


def _skill_spec_from_doc(doc: dict[str, Any]) -> SkillSpec:
    skill_id = str(doc.get("skill_id") or "").strip()
    if not skill_id:
        raise KeyError("skill spec missing skill_id")
    handler_id = str(doc.get("handler_id") or skill_id).strip() or skill_id
    return SkillSpec(
        skill_id=skill_id,
        handler_id=handler_id,
        display_name_zh=str(doc.get("display_name_zh") or "").strip(),
        description=str(doc.get("description") or "").strip(),
        category=str(doc.get("category") or "").strip(),
        idempotent=bool(doc.get("idempotent", False)),
        supports_dry_run=bool(doc.get("supports_dry_run", False)),
        inputs_schema=_normalize_items(doc.get("inputs_schema") or {}),
        outputs_schema=_normalize_items(doc.get("outputs_schema") or {}),
        tags=tuple(str(item).strip() for item in list(doc.get("tags") or []) if str(item).strip()),
    )


def list_skill_specs(*, team_id: str = "") -> tuple[SkillSpec, ...]:
    specs: list[SkillSpec] = []
    for doc in spec_loader.list_skill_docs(team_id=str(team_id or "").strip()):
        specs.append(_skill_spec_from_doc(doc))
    return tuple(specs)


def skill_spec(skill_id: str, *, team_id: str = "") -> SkillSpec:
    loaded = spec_loader.skill_doc(skill_id, team_id=str(team_id or "").strip())
    if not loaded:
        raise KeyError(f"unknown skill spec: {skill_id}")
    return _skill_spec_from_doc(loaded)
