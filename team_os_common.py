"""team_os_common.py -- Shared utilities for team-os.

Canonical definitions of functions that were duplicated across 15+ files.
All modules should import from here instead of defining their own copies.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from pathlib import Path
from typing import Any


_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def teamos_home() -> Path:
    raw = str(os.getenv("TEAMOS_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".teamos").resolve()


def looks_like_teamos_repo(root: Path) -> bool:
    markers = (
        (root / "TEAMOS.md").exists(),
        (root / "templates" / "runtime" / "orchestrator").exists(),
        (root / "schemas").exists(),
    )
    return (root / "scripts" / "pipelines").exists() and any(markers)


def team_os_root() -> Path:
    """Resolve the team-os repo root.

    Priority:
    1) env TEAM_OS_REPO_PATH
    2) relative to this file location (team_os_common.py lives at repo root)
    3) cwd walk-up
    """
    env = str(os.getenv("TEAM_OS_REPO_PATH") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if looks_like_teamos_repo(p):
            return p

    # This file lives at repo root
    here = Path(__file__).resolve().parent
    if looks_like_teamos_repo(here):
        return here

    # Walk up from cwd
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if looks_like_teamos_repo(parent):
            return parent

    raise RuntimeError("Cannot locate team-os repo root (set TEAM_OS_REPO_PATH or run from within the repo)")


def default_runtime_root() -> Path:
    return (teamos_home() / "runtime" / "default").resolve()


def runtime_root(*, override: str = "") -> Path:
    """Resolve runtime root outside repo.

    Priority:
    1) explicit override
    2) env TEAMOS_RUNTIME_ROOT
    3) ~/.teamos/runtime/default
    """
    v = str(override or "").strip()
    if not v:
        v = str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip()
    if not v:
        v = str(default_runtime_root())
    return Path(v).expanduser().resolve()


def workspace_root(*, override: str = "") -> Path:
    v = str(override or "").strip()
    if not v:
        v = str(os.getenv("TEAMOS_WORKSPACE_ROOT") or "").strip()
    if not v:
        v = str(runtime_root() / "workspace")
    return Path(v).expanduser().resolve()


def safe_project_id(project_id: str) -> str:
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    if pid != pid.lower():
        raise ValueError(f"invalid project_id={pid!r} (must be lowercase)")
    if not _PROJECT_ID_RE.match(pid):
        raise ValueError(f"invalid project_id={pid!r} (allowed: [a-z0-9][a-z0-9_-]{{0,63}})")
    if any(x in pid for x in ("/", "\\", "..")):
        raise ValueError(f"invalid project_id={pid!r}")
    return pid
