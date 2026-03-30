import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple


class ReqScriptError(Exception):
    pass


def _run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if p.returncode != 0:
        raise ReqScriptError(f"command failed: {' '.join(cmd)}: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


def repo_root() -> Path:
    """
    Best-effort OpenTeam repo root detection for local scripts.
    Priority:
    1) env OPENTEAM_REPO_PATH
    2) relative to this file location
    3) git rev-parse --show-toplevel
    """
    env = str(os.getenv("OPENTEAM_REPO_PATH") or "").strip()
    def looks_like_repo(root: Path) -> bool:
        return (root / "AGENTS.md").exists() and (root / "scripts" / "pipelines").exists()

    if env:
        p = Path(env).expanduser().resolve()
        if looks_like_repo(p):
            return p

    # This file lives at: <repo>/scripts/requirements/_common.py
    p = Path(__file__).resolve()
    try:
        candidate = p.parents[2]
        if looks_like_repo(candidate):
            return candidate
    except Exception:
        pass

    try:
        top = _run(["git", "rev-parse", "--show-toplevel"])
        p2 = Path(top).expanduser().resolve()
        if looks_like_repo(p2):
            return p2
    except Exception:
        pass

    raise ReqScriptError("Cannot locate OpenTeam repo root (set OPENTEAM_REPO_PATH or run from within the repo)")


def workspace_root() -> Path:
    v = str(os.getenv("OPENTEAM_WORKSPACE_ROOT") or "").strip()
    if not v:
        home = str(os.getenv("OPENTEAM_HOME") or "").strip()
        if home:
            base = Path(home).expanduser().resolve()
            v = str(base / "workspace")
        else:
            runtime_root = str(os.getenv("OPENTEAM_RUNTIME_ROOT") or "").strip()
            if runtime_root:
                v = str(Path(runtime_root).expanduser().resolve() / "workspace")
            else:
                v = str((Path.home() / ".openteam").resolve() / "workspace")
    return Path(v).expanduser().resolve()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def parse_scope(scope: str) -> Tuple[str, str]:
    s = str(scope or "").strip()
    if not s:
        raise ReqScriptError("scope is required: openteam | project:<id>")
    if s == "openteam":
        return ("openteam", "openteam")
    if s.startswith("project:"):
        pid = s.split(":", 1)[1].strip()
        if not pid:
            raise ReqScriptError("invalid scope: project:<id> missing <id>")
        return (s, pid)
    # Backward compatible: treat bare id as project:<id>
    return (f"project:{s}", s)


def requirements_dir(scope: str, *, ensure: bool) -> Path:
    s, pid = parse_scope(scope)
    rr = repo_root()
    if s == "openteam":
        d = rr / "docs" / "openteam" / "requirements"
        if ensure:
            (d / "baseline").mkdir(parents=True, exist_ok=True)
            (d / "conflicts").mkdir(parents=True, exist_ok=True)
        return d

    ws = workspace_root()
    if _is_within(ws, rr):
        raise ReqScriptError(f"invalid workspace_root={ws} (must be outside openteam repo={rr})")
    d = ws / "projects" / pid / "state" / "requirements"
    if ensure:
        (d / "baseline").mkdir(parents=True, exist_ok=True)
        (d / "conflicts").mkdir(parents=True, exist_ok=True)
        raw = d / "raw_inputs.jsonl"
        if not raw.exists():
            raw.write_text("", encoding="utf-8")
    return d


def add_template_app_to_syspath() -> None:
    """
    Import control-plane logic from the runtime template as the single source of truth.
    """
    rr = repo_root()
    app_dir = rr / "scaffolds" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
