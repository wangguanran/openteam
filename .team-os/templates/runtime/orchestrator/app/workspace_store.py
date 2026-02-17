from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class WorkspaceError(Exception):
    pass


_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _expand(p: str) -> Path:
    return Path(p).expanduser().resolve()


def workspace_root() -> Path:
    """
    Workspace root (outside the team-os git repo).

    Default: ~/.teamos/workspace
    Override:
    - env TEAMOS_WORKSPACE_ROOT (recommended for control-plane container)
    """
    v = str(os.getenv("TEAMOS_WORKSPACE_ROOT", "")).strip()
    if not v:
        v = str(Path.home() / ".teamos" / "workspace")
    return _expand(v)


def ensure_workspace_scaffold(root: Optional[Path] = None) -> Path:
    """
    Create the workspace directory structure (idempotent).
    """
    r = (root or workspace_root()).resolve()
    (r / "projects").mkdir(parents=True, exist_ok=True)
    (r / "shared" / "cache").mkdir(parents=True, exist_ok=True)
    (r / "shared" / "tmp").mkdir(parents=True, exist_ok=True)
    (r / "config").mkdir(parents=True, exist_ok=True)
    cfg = r / "config" / "workspace.toml"
    if not cfg.exists():
        cfg.write_text(
            "\n".join(
                [
                    "# Team OS Workspace config (local; not committed)",
                    "",
                    f'workspace_root = "{r}"',
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return r


def _safe_project_id(project_id: str) -> str:
    pid = str(project_id or "").strip()
    if not pid:
        raise WorkspaceError("project_id is required")
    if pid != pid.lower():
        raise WorkspaceError(f"invalid project_id={pid!r} (must be lowercase for cross-platform filesystem safety)")
    if not _PROJECT_ID_RE.match(pid):
        raise WorkspaceError(f"invalid project_id={pid!r} (allowed: [a-z0-9][a-z0-9_-]{{0,63}})")
    # Keep conservative; allow basic separators.
    if any(x in pid for x in ("/", "\\", "..")):
        raise WorkspaceError(f"invalid project_id={pid!r}")
    return pid


def project_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    pid = _safe_project_id(project_id)
    r = (root or workspace_root()).resolve()
    return (r / "projects" / pid).resolve()


def project_repo_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_dir(project_id, root=root) / "repo"


def project_state_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_dir(project_id, root=root) / "state"


def ledger_tasks_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "ledger" / "tasks"


def logs_tasks_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "logs" / "tasks"


def requirements_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "requirements"


def plan_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "plan"


def prompts_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "prompts"


def kb_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "kb"


def cluster_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "cluster"


def conversations_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "ledger" / "conversations" / _safe_project_id(project_id)


def ensure_project_scaffold(project_id: str, *, root: Optional[Path] = None) -> dict[str, Any]:
    """
    Create per-project directories (idempotent).

    This does not clone repos. It only ensures state folders exist.
    """
    r = ensure_workspace_scaffold(root)
    pid = _safe_project_id(project_id)
    pdir = project_dir(pid, root=r)
    (pdir / "repo").mkdir(parents=True, exist_ok=True)

    s = pdir / "state"
    (s / "ledger" / "tasks").mkdir(parents=True, exist_ok=True)
    (s / "logs" / "tasks").mkdir(parents=True, exist_ok=True)
    (s / "locks").mkdir(parents=True, exist_ok=True)
    (s / "requirements" / "conflicts").mkdir(parents=True, exist_ok=True)
    (s / "requirements" / "baseline").mkdir(parents=True, exist_ok=True)
    raw = s / "requirements" / "raw_inputs.jsonl"
    if not raw.exists():
        raw.write_text("", encoding="utf-8")
    (s / "prompts").mkdir(parents=True, exist_ok=True)
    (s / "kb").mkdir(parents=True, exist_ok=True)
    (s / "cluster").mkdir(parents=True, exist_ok=True)

    # Minimal prompt skeleton (project-scoped, lives in workspace).
    mp = s / "prompts" / "MASTER_PROMPT.md"
    if not mp.exists():
        mp.write_text(f"# MASTER PROMPT ({pid})\n\n- TODO\n", encoding="utf-8")

    # Minimal plan skeleton (optional).
    plan = s / "plan" / "plan.yaml"
    if not plan.parent.exists():
        plan.parent.mkdir(parents=True, exist_ok=True)
    if not plan.exists():
        plan.write_text(f"schema_version: 1\nproject_id: \"{pid}\"\nmilestones: []\n", encoding="utf-8")
    md = s / "plan" / "PLAN.md"
    if not md.exists():
        md.write_text(f"# PLAN ({pid})\n\n- TODO\n", encoding="utf-8")

    return {"workspace_root": str(r), "project_id": pid, "project_dir": str(pdir), "state_dir": str(s)}


def list_projects(*, root: Optional[Path] = None) -> list[str]:
    r = (root or workspace_root()).resolve()
    d = r / "projects"
    if not d.exists():
        return []
    out = []
    for x in sorted(d.iterdir()):
        if x.is_dir():
            out.append(x.name)
    return out


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def assert_project_paths_outside_repo(*, team_os_root: Path, workspace_root_path: Optional[Path] = None) -> None:
    """
    Defensive check: workspace must NOT be within team-os repo, otherwise any project writes would pollute git repo.
    """
    ws = (workspace_root_path or workspace_root()).resolve()
    tr = team_os_root.resolve()
    if _is_within(ws, tr):
        raise WorkspaceError(f"invalid workspace_root={ws} (must be outside team-os repo={tr})")
