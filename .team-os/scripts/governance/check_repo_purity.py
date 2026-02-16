#!/usr/bin/env python3
"""
Repo Purity Check

Hard governance rule:
- `team-os/` git repo must ONLY contain Team OS itself.
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

import yaml


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


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _infer_project_id_for_task(tasks_dir: Path, *, task_id: str) -> str:
    """
    Best-effort inference for legacy/backup ledgers missing project_id:
    - Prefer the canonical ledger `<task_id>.yaml` if present.
    """
    try:
        base = tasks_dir / f"{task_id}.yaml"
        if base.exists():
            data = _read_yaml(base)
            pid = str(data.get("project_id") or "").strip()
            if pid:
                return pid
    except Exception:
        pass
    return ""


def check_repo_purity(repo_root: Path) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []

    def add(kind: str, path: Path, detail: str) -> None:
        violations.append({"kind": kind, "path": str(path), "detail": detail})

    # 1) obvious in-repo project roots
    if _is_dir(repo_root / "projects"):
        add("IN_REPO_PROJECTS_DIR", repo_root / "projects", "Workspace projects must not be inside team-os repo")

    if _is_dir(repo_root / "prompt-library" / "projects"):
        add("IN_REPO_PROJECT_PROMPTS", repo_root / "prompt-library" / "projects", "Project prompts must live in workspace")

    # 2) docs/requirements must not exist in repo (projects moved; teamos self lives under docs/teamos/)
    if _is_dir(repo_root / "docs" / "requirements"):
        add("IN_REPO_REQUIREMENTS_DIR", repo_root / "docs" / "requirements", "Project requirements must live in workspace; teamos lives under docs/teamos/")

    # 3) docs/plan may only contain teamos (project plans must live in workspace)
    plan = repo_root / "docs" / "plan"
    if _is_dir(plan):
        for d in sorted(plan.iterdir()):
            if not d.is_dir():
                continue
            if d.name == "teamos":
                continue
            add("IN_REPO_PROJECT_PLAN", d, "Project plan overlay must live in workspace")

    # 4) conversations are project-scoped; only allow teamos (or none)
    conv = repo_root / ".team-os" / "ledger" / "conversations"
    if _is_dir(conv):
        for d in sorted(conv.iterdir()):
            if not d.is_dir():
                continue
            if d.name == "teamos":
                continue
            add("IN_REPO_PROJECT_CONVERSATIONS", d, "Project conversations must live in workspace")

    # 5) task ledgers/logs must not include non-teamos projects
    tasks_dir = repo_root / ".team-os" / "ledger" / "tasks"
    task_project: dict[str, str] = {}
    if _is_dir(tasks_dir):
        for y in sorted(tasks_dir.glob("*.yaml")):
            data = _read_yaml(y)
            tid = str(data.get("id") or y.stem)
            pid = str(data.get("project_id") or "").strip() or "(missing)"
            task_project[tid] = pid
            if pid != "teamos":
                add("IN_REPO_PROJECT_TASK_LEDGER", y, f"task_id={tid} project_id={pid} must live in workspace")

        # Backup ledgers created in-repo are still project-scoped data and must not exist here.
        for y in sorted(tasks_dir.glob("*.yaml.bak.*")):
            data = _read_yaml(y)
            tid = str(data.get("id") or y.name.split(".yaml", 1)[0])
            pid = str(data.get("project_id") or "").strip()
            if not pid:
                pid = _infer_project_id_for_task(tasks_dir, task_id=tid)
            pid = pid or "(missing)"
            if pid != "teamos":
                add("IN_REPO_PROJECT_TASK_LEDGER_BACKUP", y, f"task_id={tid} project_id={pid} backup must live in workspace")

    logs_dir = repo_root / ".team-os" / "logs" / "tasks"
    if _is_dir(logs_dir):
        for d in sorted(logs_dir.iterdir()):
            if not d.is_dir():
                continue
            tid = d.name
            pid = task_project.get(tid, "(missing_ledger)")
            if pid != "teamos":
                add("IN_REPO_PROJECT_TASK_LOGS", d, f"task_id={tid} project_id={pid} must live in workspace")

    ok = not violations
    return {"ok": ok, "repo_root": str(repo_root), "violations": violations}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Team OS repo purity checker (no project artifacts in repo)")
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
                print("  teamos workspace migrate --from-repo  # dry-run plan")
                print("  teamos workspace migrate --from-repo --force  # apply (high risk)")
        else:
            print(f"repo_purity.ok={str(out['ok']).lower()} violations={len(out['violations'])}")

    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
