#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, is_within, resolve_repo_root, resolve_workspace_root, safe_project_id


def _ensure_writable(tmp_dir: Path) -> None:
    t = tmp_dir / f"doctor_{os.getpid()}.tmp"
    t.write_text("ok\n", encoding="utf-8")
    t.unlink(missing_ok=True)


def check_workspace(*, repo_root: Path, workspace_root: Path) -> dict[str, Any]:
    if not workspace_root.exists():
        return {"ok": False, "reason": "missing_root", "workspace_root": str(workspace_root)}
    if is_within(workspace_root, repo_root):
        return {"ok": False, "reason": "workspace_inside_repo", "workspace_root": str(workspace_root), "repo_root": str(repo_root)}

    must = [
        workspace_root / "projects",
        workspace_root / "shared" / "cache",
        workspace_root / "shared" / "tmp",
        workspace_root / "config",
    ]
    miss = [str(p) for p in must if not p.exists()]
    if miss:
        return {"ok": False, "reason": "missing_paths", "missing": miss[:20], "workspace_root": str(workspace_root)}
    try:
        _ensure_writable(workspace_root / "shared" / "tmp")
    except Exception as e:
        return {"ok": False, "reason": "not_writable", "error": str(e)[:200], "workspace_root": str(workspace_root)}

    bad_projects: list[str] = []
    missing_by_project: dict[str, list[str]] = {}
    projects_dir = workspace_root / "projects"
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir():
                continue
            pid = d.name
            try:
                safe_project_id(pid)
            except Exception:
                bad_projects.append(pid)
                continue
            must_paths = [
                d / "repo",
                d / "state" / "ledger" / "tasks",
                d / "state" / "logs" / "tasks",
                d / "state" / "requirements" / "conflicts",
                d / "state" / "requirements" / "baseline",
                d / "state" / "requirements" / "raw_inputs.jsonl",
                d / "state" / "prompts" / "MASTER_PROMPT.md",
                d / "state" / "plan" / "plan.yaml",
                d / "state" / "plan" / "PLAN.md",
                d / "state" / "kb",
                d / "state" / "cluster",
                d / "state" / "requirements" / "requirements.yaml",
            ]
            miss2 = [str(p.relative_to(d)) for p in must_paths if not p.exists()]
            if miss2:
                missing_by_project[pid] = miss2

    if bad_projects:
        return {"ok": False, "reason": "invalid_project_ids", "invalid_project_ids": bad_projects[:20], "workspace_root": str(workspace_root)}
    if missing_by_project:
        first = sorted(missing_by_project.keys())[0]
        return {"ok": False, "reason": "missing_project_paths", "project_id": first, "missing": missing_by_project[first][:20], "workspace_root": str(workspace_root)}

    return {"ok": True, "workspace_root": str(workspace_root)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Workspace doctor (pipeline)")
    add_default_args(ap)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    out = check_workspace(repo_root=repo, workspace_root=ws)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        ok = bool(out.get("ok"))
        print(f"workspace_root={out.get('workspace_root','')}")
        print(f"workspace.ok={str(ok).lower()}")
        if not ok:
            print(f"reason={out.get('reason','')}")
            if out.get("missing"):
                print(f"missing={out.get('missing')}")
            if out.get("invalid_project_ids"):
                print(f"invalid_project_ids={out.get('invalid_project_ids')}")
            if out.get("project_id"):
                print(f"project_id={out.get('project_id')}")
    return 0 if bool(out.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())

