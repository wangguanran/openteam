from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class WorkspaceError(Exception):
    pass


_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _expand(p: str) -> Path:
    return Path(p).expanduser().resolve()


def _teamos_home() -> Path:
    raw = str(os.getenv("TEAMOS_HOME") or "").strip()
    if raw:
        return _expand(raw)
    return (Path.home() / ".teamos").resolve()


def team_os_root() -> Path:
    env = str(os.getenv("TEAM_OS_REPO_PATH") or "").strip()
    if env:
        return _expand(env)

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
    v = str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip()
    if v:
        return _expand(v)
    return (_teamos_home() / "runtime" / "default").resolve()


def workspace_root() -> Path:
    """
    Workspace root (outside the team-os git repo).

    Default: <runtime_root>/workspace
    Override:
    - env TEAMOS_WORKSPACE_ROOT (recommended for control-plane container)
    """
    v = str(os.getenv("TEAMOS_WORKSPACE_ROOT", "")).strip()
    if not v:
        v = str(runtime_root() / "workspace")
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


def legacy_targets_dir(*, root: Optional[Path] = None) -> Path:
    r = (root or workspace_root()).resolve()
    return (r / "targets").resolve()


def project_targets_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_dir(project_id, root=root) / "targets"


def legacy_target_dir(target_id: str, *, root: Optional[Path] = None) -> Path:
    tid = _safe_project_id(target_id)
    return (legacy_targets_dir(root=root) / tid).resolve()


def target_dir(target_id: str, *, project_id: str = "teamos", root: Optional[Path] = None) -> Path:
    tid = _safe_project_id(target_id)
    return (project_targets_dir(project_id, root=root) / tid).resolve()


def target_repo_dir(target_id: str, *, project_id: str = "teamos", root: Optional[Path] = None) -> Path:
    return target_dir(target_id, project_id=project_id, root=root) / "repo"


def target_state_dir(target_id: str, *, project_id: str = "teamos", root: Optional[Path] = None) -> Path:
    return target_dir(target_id, project_id=project_id, root=root) / "state"


def project_state_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_dir(project_id, root=root) / "state"


def ledger_tasks_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "ledger" / "tasks"


def logs_tasks_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_state_dir(project_id, root=root) / "logs" / "tasks"


def logs_team_dir(project_id: str, team_id: str, *, root: Optional[Path] = None) -> Path:
    safe_team_id = _safe_project_id(str(team_id or "team").replace("-", "_"))
    return project_state_dir(project_id, root=root) / "logs" / "teams" / safe_team_id


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
    (pdir / "targets").mkdir(parents=True, exist_ok=True)

    s = pdir / "state"
    (s / "ledger" / "tasks").mkdir(parents=True, exist_ok=True)
    (s / "logs" / "tasks").mkdir(parents=True, exist_ok=True)
    (s / "logs" / "teams").mkdir(parents=True, exist_ok=True)
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


def _merge_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _merge_tree(item, target)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item), str(target))
    try:
        src.rmdir()
    except Exception:
        pass


def _migrate_legacy_target_layout(*, project_id: str, target_id: str, root: Optional[Path] = None) -> None:
    legacy = legacy_target_dir(target_id, root=root)
    current = target_dir(target_id, project_id=project_id, root=root)
    if not legacy.exists() or legacy.resolve() == current.resolve():
        return
    current.parent.mkdir(parents=True, exist_ok=True)
    if not current.exists():
        shutil.move(str(legacy), str(current))
        return
    for name in ("repo", "state"):
        _merge_tree(legacy / name, current / name)
    for item in list(legacy.iterdir()) if legacy.exists() else []:
        _merge_tree(item, current / item.name)
    try:
        legacy.rmdir()
    except Exception:
        pass


def ensure_target_scaffold(target_id: str, *, project_id: str = "teamos", root: Optional[Path] = None) -> dict[str, Any]:
    r = ensure_workspace_scaffold(root)
    pid = _safe_project_id(project_id)
    tid = _safe_project_id(target_id)
    ensure_project_scaffold(pid, root=r)
    _migrate_legacy_target_layout(project_id=pid, target_id=tid, root=r)
    tdir = target_dir(tid, project_id=pid, root=r)
    (tdir / "repo").mkdir(parents=True, exist_ok=True)
    (tdir / "state").mkdir(parents=True, exist_ok=True)
    return {
        "workspace_root": str(r),
        "project_id": pid,
        "target_id": tid,
        "target_dir": str(tdir),
        "repo_dir": str((tdir / "repo").resolve()),
        "state_dir": str((tdir / "state").resolve()),
    }


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
