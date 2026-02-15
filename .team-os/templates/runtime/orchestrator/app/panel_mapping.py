import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from .state_store import github_projects_mapping_path


class PanelMappingError(Exception):
    pass


@dataclass(frozen=True)
class MappingDoc:
    path: Path
    sha256: str
    data: dict[str, Any]


def load_mapping() -> MappingDoc:
    p = github_projects_mapping_path()
    if not p.exists():
        raise PanelMappingError(f"missing mapping.yaml: {p}")
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    try:
        data = yaml.safe_load(raw.decode("utf-8")) or {}
    except Exception as e:
        raise PanelMappingError(f"invalid yaml: {p}: {e}") from e
    if not isinstance(data, dict):
        raise PanelMappingError(f"mapping.yaml must be a mapping/object: {p}")
    return MappingDoc(path=p, sha256=sha, data=data)


def get_project_cfg(mapping: MappingDoc, project_id: str) -> Optional[dict[str, Any]]:
    projects = mapping.data.get("projects") or {}
    if not isinstance(projects, dict):
        return None
    return projects.get(project_id)

