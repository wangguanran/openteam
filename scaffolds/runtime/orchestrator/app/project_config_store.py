from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import workspace_store


def project_config_path(project_id: str) -> Path:
    return workspace_store.project_state_dir(project_id) / "config" / "project.yaml"


def load_project_config(project_id: str) -> dict[str, Any]:
    path = project_config_path(project_id)
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}
