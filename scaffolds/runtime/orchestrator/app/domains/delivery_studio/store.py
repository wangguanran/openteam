from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app import workspace_store


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def save_request(project_id: str, request_id: str, payload: dict[str, Any]) -> Path:
    path = workspace_store.delivery_request_dir(project_id, request_id) / "request.yaml"
    _write_yaml(path, payload)
    return path


def load_request(project_id: str, request_id: str) -> dict[str, Any]:
    path = workspace_store.delivery_request_dir(project_id, request_id) / "request.yaml"
    if not path.exists():
        raise FileNotFoundError(path)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
