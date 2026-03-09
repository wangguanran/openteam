import os
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml


class StateError(Exception):
    pass


def team_os_root() -> Path:
    """
    Team OS repo root.

    In container runtime this is injected via TEAM_OS_REPO_PATH (default mount: /team-os).
    For local execution (unit tests / scripts) we fall back to discovering the repo root by
    walking up from this file until we find Team-OS repo markers.
    """
    env = str(os.getenv("TEAM_OS_REPO_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()

    p = Path(__file__).resolve()
    for parent in [p.parent] + list(p.parents):
        if (parent / "scripts" / "pipelines").exists() and (
            (parent / "TEAMOS.md").exists()
            or (parent / "templates" / "runtime" / "orchestrator").exists()
            or (parent / "schemas").exists()
        ):
            return parent.resolve()

    return Path("/team-os").resolve()


def runtime_root() -> Path:
    """
    Runtime root contract:
    - env TEAMOS_RUNTIME_ROOT
    - default: <team_os_root>/../team-os-runtime
    """
    v = str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip()
    if v:
        return Path(v).expanduser().resolve()
    return (team_os_root().parent / "team-os-runtime").resolve()


def runtime_state_root() -> Path:
    return runtime_root() / "state"


def state_dir() -> Path:
    return runtime_state_root()


def ledger_tasks_dir() -> Path:
    # Team OS self task ledgers (scope=teamos).
    return runtime_state_root() / "ledger" / "tasks"


def logs_tasks_dir() -> Path:
    # Team OS self task logs (scope=teamos).
    return runtime_state_root() / "logs" / "tasks"


def teamos_requirements_dir() -> Path:
    # Team OS self requirements truth source (scope=teamos).
    # Project requirements must live in Workspace.
    return team_os_root() / "docs" / "teamos" / "requirements"


def teamos_plan_dir() -> Path:
    # Team OS self planning overlay (scope=teamos).
    return team_os_root() / "docs" / "plan" / "teamos"


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_instance_id() -> str:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "instance_id"
    if p.exists():
        v = p.read_text(encoding="utf-8").strip()
        if v:
            return v
    v = str(uuid.uuid4())
    _write_text(p, v + "\n")
    return v


def load_focus() -> dict[str, Any]:
    d = state_dir()
    y = d / "focus.yaml"
    if not y.exists():
        # Keep a sensible default without writing unless needed.
        return {
            "objective": "",
            "scope": [],
            "constraints": [],
            "success_metrics": [],
            "updated_at": "1970-01-01T00:00:00Z",
            "source": "missing",
        }
    return _read_yaml(y)


def save_focus(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    d = state_dir()
    y = d / "focus.yaml"
    md = d / "FOCUS.md"
    data = load_focus()
    data["objective"] = str(payload.get("objective", data.get("objective", ""))).strip()
    data["scope"] = list(payload.get("scope") or data.get("scope") or [])
    data["constraints"] = list(payload.get("constraints") or data.get("constraints") or [])
    data["success_metrics"] = list(payload.get("success_metrics") or data.get("success_metrics") or [])
    data["updated_at"] = _utc_now_iso()
    data["source"] = source
    _write_yaml(y, data)
    _write_text(md, render_focus_md(data))
    return data


def render_focus_md(focus: dict[str, Any]) -> str:
    obj = focus.get("objective", "")
    updated_at = focus.get("updated_at", "")
    source = focus.get("source", "")
    scope = focus.get("scope") or []
    constraints = focus.get("constraints") or []
    metrics = focus.get("success_metrics") or []
    lines: list[str] = [
        "# Current Focus",
        "",
        f"- objective: {obj}",
        f"- updated_at: {updated_at}",
        f"- source: {source}",
        "",
        "## Scope",
        "",
    ]
    lines += [f"- {x}" for x in scope] or ["- (none)"]
    lines += ["", "## Constraints", ""]
    lines += [f"- {x}" for x in constraints] or ["- (none)"]
    lines += ["", "## Success Metrics", ""]
    lines += [f"- {x}" for x in metrics] or ["- (none)"]
    lines.append("")
    return "\n".join(lines)


def load_workstreams() -> list[dict[str, Any]]:
    p = state_dir() / "workstreams.yaml"
    data = _read_yaml(p)
    return list(data.get("workstreams") or [])


def github_projects_mapping_path() -> Path:
    return team_os_root() / "integrations" / "github_projects" / "mapping.yaml"
