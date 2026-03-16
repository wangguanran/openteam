from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def role_specs_root() -> Path:
    return (app_root() / "role_library" / "specs").resolve()


def skill_specs_root() -> Path:
    return (app_root() / "skill_library" / "specs").resolve()


def teams_root() -> Path:
    return (app_root() / "teams").resolve()


def _normalize_team_dirname(team_id: str) -> str:
    return str(team_id or "").strip().replace("-", "_")


def team_root(team_id: str) -> Path:
    return (teams_root() / _normalize_team_dirname(team_id)).resolve()


def team_specs_root(team_id: str) -> Path:
    return (team_root(team_id) / "specs").resolve()


def _load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _sorted_yaml_docs(root: Path) -> tuple[dict[str, Any], ...]:
    if not root.exists():
        return ()
    docs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        doc = _load_yaml_file(path)
        if doc:
            docs.append(doc)
    return tuple(docs)


@lru_cache(maxsize=8)
def list_spec_docs(kind: str) -> tuple[dict[str, Any], ...]:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "roles":
        return _sorted_yaml_docs(role_specs_root())
    if normalized_kind == "skills":
        return _sorted_yaml_docs(skill_specs_root())
    if normalized_kind == "teams":
        docs: list[dict[str, Any]] = []
        for path in sorted(teams_root().glob("*/specs/team.yaml")):
            doc = _load_yaml_file(path)
            if doc:
                docs.append(doc)
        return tuple(docs)
    if normalized_kind == "tasks":
        docs: list[dict[str, Any]] = []
        for path in sorted(teams_root().glob("*/specs/tasks/*.yaml")):
            doc = _load_yaml_file(path)
            if doc:
                docs.append(doc)
        return tuple(docs)
    return ()


@lru_cache(maxsize=32)
def spec_doc_by_key(kind: str, key_name: str, key_value: str) -> dict[str, Any]:
    wanted = str(key_value or "").strip()
    if not wanted:
        return {}
    for doc in list_spec_docs(kind):
        if str(doc.get(key_name) or "").strip() == wanted:
            return dict(doc)
    return {}


@lru_cache(maxsize=16)
def list_team_workflow_docs(team_id: str) -> tuple[dict[str, Any], ...]:
    wanted = str(team_id or "").strip()
    if not wanted:
        return ()
    return _sorted_yaml_docs(team_specs_root(wanted) / "workflows")


@lru_cache(maxsize=16)
def list_team_stage_docs(team_id: str) -> tuple[dict[str, Any], ...]:
    wanted = str(team_id or "").strip()
    if not wanted:
        return ()
    return _sorted_yaml_docs(team_specs_root(wanted) / "stages")


def role_doc(role_id: str) -> dict[str, Any]:
    return spec_doc_by_key("roles", "role_id", role_id)


def list_role_docs() -> tuple[dict[str, Any], ...]:
    return list_spec_docs("roles")


def skill_doc(skill_id: str) -> dict[str, Any]:
    return spec_doc_by_key("skills", "skill_id", skill_id)


def list_skill_docs() -> tuple[dict[str, Any], ...]:
    return list_spec_docs("skills")


def team_doc(team_id: str) -> dict[str, Any]:
    wanted = str(team_id or "").strip()
    if not wanted:
        return {}
    nested = _load_yaml_file(team_specs_root(wanted) / "team.yaml")
    if nested:
        return nested
    return spec_doc_by_key("teams", "team_id", wanted)


def team_workflow_doc(team_id: str, workflow_id: str) -> dict[str, Any]:
    wanted_team = str(team_id or "").strip()
    wanted_workflow = str(workflow_id or "").strip()
    if not wanted_team or not wanted_workflow:
        return {}
    return _load_yaml_file(team_specs_root(wanted_team) / "workflows" / f"{wanted_workflow}.yaml")


def team_stage_doc(team_id: str, stage_id: str) -> dict[str, Any]:
    wanted_team = str(team_id or "").strip()
    wanted_stage = str(stage_id or "").strip()
    if not wanted_team or not wanted_stage:
        return {}
    return _load_yaml_file(team_specs_root(wanted_team) / "stages" / f"{wanted_stage}.yaml")


def task_doc(task_name: str) -> dict[str, Any]:
    return spec_doc_by_key("tasks", "task_name", task_name)


def clear_spec_caches() -> None:
    list_spec_docs.cache_clear()
    spec_doc_by_key.cache_clear()
    list_team_workflow_docs.cache_clear()
    list_team_stage_docs.cache_clear()
