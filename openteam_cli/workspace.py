"""Workspace subcommand handlers."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from openteam_cli._shared import (
    _approval_gate,
    _ensure_workspace_scaffold,
    _find_openteam_repo_root,
    _is_safe_project_id,
    _is_within,
    _workspace_root,
)


def cmd_workspace_init(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser().resolve() if getattr(args, "path", "") else _workspace_root(args)
    _ensure_workspace_scaffold(path)
    print(f"workspace_root={path}")
    print("workspace_init: OK")


def cmd_workspace_show(args: argparse.Namespace) -> None:
    root = _workspace_root(args)
    projects_dir = root / "projects"
    projects = []
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir()):
            if d.is_dir():
                projects.append(d.name)
    print(f"workspace_root={root}")
    print(f"projects_count={len(projects)}")
    if projects:
        for pid in projects[:200]:
            pdir = projects_dir / pid
            repo_ok = (pdir / "repo").exists()
            state_ok = (pdir / "state").exists()
            print(f"- {pid} repo={repo_ok} state={state_ok}")


def cmd_workspace_doctor(args: argparse.Namespace) -> None:
    root = _workspace_root(args)
    if not root.exists():
        print(f"workspace: FAIL missing_root={root}")
        print("next: openteam workspace init")
        raise SystemExit(2)

    # Governance: workspace must be OUTSIDE the openteam repo.
    repo_root = _find_openteam_repo_root()
    if repo_root and _is_within(root, repo_root):
        print(f"workspace: FAIL workspace_root_inside_repo root={root} repo={repo_root}")
        print("next: openteam workspace init --path ~/.openteam/workspace")
        raise SystemExit(2)

    must = [
        root / "projects",
        root / "shared" / "cache",
        root / "shared" / "tmp",
        root / "config",
    ]
    miss = [str(p) for p in must if not p.exists()]
    if miss:
        print("workspace: FAIL missing_paths=" + ",".join(miss[:5]))
        print("next: openteam workspace init")
        raise SystemExit(2)
    # Basic writability check.
    try:
        t = root / "shared" / "tmp" / f"doctor_{os.getpid()}.tmp"
        t.write_text("ok\n", encoding="utf-8")
        t.unlink(missing_ok=True)
    except Exception as e:
        print(f"workspace: FAIL not_writable err={e}")
        raise SystemExit(2)
    print(f"workspace_root={root}")

    # Per-project structure checks.
    bad_projects: list[str] = []
    missing_by_project: dict[str, list[str]] = {}
    projects_dir = root / "projects"
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir():
                continue
            pid = d.name
            if not _is_safe_project_id(pid):
                bad_projects.append(pid)
                continue
            req = d / "state" / "requirements" / "requirements.yaml"
            must_paths = [
                d / "repo",
                d / "state" / "ledger" / "tasks",
                d / "state" / "logs" / "tasks",
                d / "state" / "requirements" / "conflicts",
                d / "state" / "prompts" / "MASTER_PROMPT.md",
                d / "state" / "plan" / "plan.yaml",
                d / "state" / "plan" / "PLAN.md",
                d / "state" / "kb",
                req,
            ]
            miss = [str(p.relative_to(d)) for p in must_paths if not p.exists()]
            if miss:
                missing_by_project[pid] = miss
    if bad_projects:
        print("workspace: FAIL invalid_project_ids=" + ",".join(bad_projects[:10]))
        print("next: rename project dirs to lowercase [a-z0-9][a-z0-9_-]{0,63}")
        raise SystemExit(2)
    if missing_by_project:
        first = sorted(missing_by_project.keys())[0]
        print(f"workspace: FAIL missing_project_paths project_id={first} missing={missing_by_project[first][:8]}")
        print("next: openteam workspace init  # idempotent repair")
        raise SystemExit(2)

    print("workspace: OK")


def cmd_workspace_migrate(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root (for --from-repo migration).")
    if not getattr(args, "from_repo", False):
        raise RuntimeError("Only supported mode: --from-repo")

    root = _workspace_root(args)
    # Local governance script (no remote writes).
    script = repo_root / "scripts" / "governance" / "migrate_repo_projects.py"
    if not script.exists():
        raise RuntimeError(f"migration script missing: {script}")

    apply = bool(getattr(args, "force", False))
    if apply:
        _approval_gate(
            args,
            repo_root=repo_root,
            action_kind="workspace_migrate_force",
            summary="workspace migrate --from-repo --force (move legacy project artifacts out of openteam repo)",
            payload={"from_repo": True, "workspace_root": str(root)},
            yes=bool(getattr(args, "yes", False)),
        )

    cmd = [
        sys.executable,
        str(script),
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(root),
    ]
    if getattr(args, "dry_run", False) or (not apply):
        cmd.append("--dry-run")
    if apply:
        cmd.append("--force")
    p = subprocess.run(cmd, check=False)
    if p.returncode != 0:
        raise SystemExit(p.returncode)
