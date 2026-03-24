#!/usr/bin/env python3
"""
Repo Purity Check

Hard governance rule:
- `openteam/` git repo must ONLY contain OpenTeam itself.
- Runtime dynamic outputs must live outside repo under runtime root.
- Any `project:<id>` truth-source artifacts must live in the Workspace (outside the repo).

This checker intentionally errs on the strict side to prevent regressions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

def _repo_root_from_git(cwd: Path) -> Path:
    p = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if p.returncode == 0:
        out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
        if out:
            return Path(out).resolve()
    # fallback: best-effort
    return cwd.resolve()


def _is_dir(p: Path) -> bool:
    try:
        return p.exists() and p.is_dir()
    except Exception:
        return False


# Root-level static directories intentionally allowed inside openteam git repo.
# This is a governance reference list for reviewers; checker only denies known-dynamic roots.
_ROOT_STATIC_ALLOWLIST = {
    ".git",
    ".github",
    "docs",
    "evals",
    "integrations",
    "scaffolds",
    "scripts",
    "specs",
    "templates",
    "tests",
    "tooling",
}

# Root-level runtime/dynamic directories forbidden in repo.
# These must live under runtime root (default: ../openteam-runtime, override: OPENTEAM_RUNTIME_ROOT).
_ROOT_DYNAMIC_DENYLIST: dict[str, tuple[str, str]] = {
    ".openteam": (
        "LEGACY_OPENTEAM_DIR",
        "legacy .openteam directory is forbidden in repo; move dynamic data under runtime root",
    ),
    "openteam-runtime": (
        "IN_REPO_RUNTIME_ROOT",
        "runtime root must be outside repo (default: ../openteam-runtime)",
    ),
    "runtime": (
        "IN_REPO_DYNAMIC_RUNTIME_PATH",
        "runtime dynamic root must be outside repo",
    ),
    "workspace": (
        "IN_REPO_DYNAMIC_WORKSPACE_PATH",
        "workspace dynamic root must be outside repo",
    ),
    "hub": (
        "IN_REPO_DYNAMIC_HUB_PATH",
        "hub dynamic root must be outside repo",
    ),
    "state": (
        "IN_REPO_DYNAMIC_STATE_PATH",
        "state dynamic root must be outside repo",
    ),
    "logs": (
        "IN_REPO_DYNAMIC_LOGS_PATH",
        "logs dynamic root must be outside repo",
    ),
    "ledger": (
        "IN_REPO_DYNAMIC_LEDGER_PATH",
        "ledger dynamic root must be outside repo",
    ),
    "tasks": (
        "IN_REPO_DYNAMIC_TASKS_PATH",
        "tasks dynamic root must be outside repo",
    ),
}


def check_repo_purity(repo_root: Path) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []

    def add(kind: str, path: Path, detail: str) -> None:
        violations.append({"kind": kind, "path": str(path), "detail": detail})

    # 0) hard deny runtime/dynamic roots in repo.
    for rel, (kind, detail) in sorted(_ROOT_DYNAMIC_DENYLIST.items()):
        p = repo_root / rel
        if p.exists():
            add(kind, p, detail)

    # 1) obvious in-repo project roots
    if _is_dir(repo_root / "projects"):
        add("IN_REPO_PROJECTS_DIR", repo_root / "projects", "Workspace projects must not be inside openteam repo")

    legacy_project_prompts = repo_root / "prompt-library" / "projects"
    if _is_dir(legacy_project_prompts):
        add("IN_REPO_PROJECT_PROMPTS", legacy_project_prompts, "Project prompts must live in workspace")

    scoped_project_prompts = repo_root / "specs" / "prompts" / "projects"
    if _is_dir(scoped_project_prompts):
        add("IN_REPO_PROJECT_PROMPTS", scoped_project_prompts, "Project prompts must live in workspace")

    # 2) docs/requirements must not exist in repo (projects moved; openteam self lives under docs/product/openteam/)
    if _is_dir(repo_root / "docs" / "requirements"):
        add("IN_REPO_REQUIREMENTS_DIR", repo_root / "docs" / "requirements", "Project requirements must live in workspace; openteam lives under docs/product/openteam/")

    # 3) docs/plans may only contain openteam (project plans must live in workspace)
    plan = repo_root / "docs" / "plans"
    if _is_dir(plan):
        for d in sorted(plan.iterdir()):
            if not d.is_dir():
                continue
            if d.name == "openteam":
                continue
            add("IN_REPO_PROJECT_PLAN", d, "Project plan overlay must live in workspace")

    ok = not violations
    return {
        "ok": ok,
        "repo_root": str(repo_root),
        "root_allowlist": sorted(_ROOT_STATIC_ALLOWLIST),
        "root_denylist": sorted(_ROOT_DYNAMIC_DENYLIST.keys()),
        "violations": violations,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="OpenTeam repo purity checker (no project artifacts in repo)")
    ap.add_argument("--repo-root", default="", help="override repo root (default: git rev-parse)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else _repo_root_from_git(Path.cwd())
    out = check_repo_purity(repo_root)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if not args.quiet:
            print(f"repo_root={repo_root}")
            if out["ok"]:
                print("repo_purity.ok=true")
            else:
                print(f"repo_purity.ok=false violations={len(out['violations'])}")
                for v in out["violations"][:200]:
                    print(f"- {v['kind']}: {v['path']} :: {v['detail']}")
                print("")
                print("next:")
                print("  openteam workspace migrate --from-repo  # dry-run plan")
                print("  openteam workspace migrate --from-repo --force  # apply (high risk)")
        else:
            print(f"repo_purity.ok={str(out['ok']).lower()} violations={len(out['violations'])}")

    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
