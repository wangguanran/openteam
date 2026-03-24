#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import locks

from _common import PipelineError, add_default_args, resolve_repo_root, resolve_workspace_root, utc_now_iso, validate_or_die


def _add_runtime_template_to_syspath(repo: Path) -> None:
    app_dir = repo / "scaffolds" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


def _requirements_dir_for_scope(repo: Path, *, scope: str, workspace_root: Path) -> tuple[str, str, Path]:
    s = str(scope or "").strip()
    if not s:
        raise PipelineError("missing --scope openteam|project:<id>")
    if s == "openteam":
        return ("openteam", "openteam", repo / "docs" / "openteam" / "requirements")
    if s.startswith("project:"):
        pid = s.split(":", 1)[1].strip()
        if not pid:
            raise PipelineError("invalid scope: project:<id> missing id")
        from _common import is_within, safe_project_id

        pid = safe_project_id(pid)
        if is_within(workspace_root, repo):
            raise PipelineError(f"invalid workspace_root={workspace_root} (must be outside repo={repo})")
        return (s, pid, workspace_root / "projects" / pid / "state" / "requirements")
    return _requirements_dir_for_scope(repo, scope=f"project:{s}", workspace_root=workspace_root)


def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="System requirements update channel (non-raw; no raw_inputs writes)")
    add_default_args(ap)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--workstream", default="general")
    ap.add_argument("--priority", default="P2")
    ap.add_argument("--source", default="SYSTEM")
    ap.add_argument("--rationale", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    scope, pid, req_dir = _requirements_dir_for_scope(repo, scope=str(args.scope), workspace_root=ws_root)
    req_dir.mkdir(parents=True, exist_ok=True)

    # Keep deterministic/offline by default.
    os.environ.setdefault("OPENTEAM_REQUIREMENTS_SEMANTIC_CHECK", "0")

    raw_path = req_dir / "raw_inputs.jsonl"
    before_raw_lines = _count_jsonl_lines(raw_path)

    _add_runtime_template_to_syspath(repo)
    from app.requirements_store import add_requirement_system_update  # type: ignore

    out = None
    if not args.dry_run:
        repo_lock = None
        scope_lock = None
        try:
            if scope == "openteam":
                repo_lock = locks.acquire_repo_lock(repo_root=repo, task_id=str(os.getenv("OPENTEAM_TASK_ID") or ""))
            scope_lock = locks.acquire_scope_lock(
                scope,
                repo_root=repo,
                workspace_root=ws_root,
                req_dir=req_dir,
                task_id=str(os.getenv("OPENTEAM_TASK_ID") or ""),
            )
            out = add_requirement_system_update(
                project_id=pid,
                req_dir=req_dir,
                requirement_text=str(args.text or "").rstrip(),
                workstream_id=str(args.workstream or "").strip(),
                priority=str(args.priority or "P2"),
                rationale=str(args.rationale or ""),
                constraints=None,
                acceptance=None,
                source=str(args.source or "SYSTEM"),
            )
        finally:
            locks.release_lock(scope_lock)
            locks.release_lock(repo_lock)

    after_raw_lines = _count_jsonl_lines(raw_path)

    # Validate Expanded requirements schema (if present).
    y = req_dir / "requirements.yaml"
    if y.exists():
        import yaml

        data: dict[str, Any] = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
        validate_or_die(data, repo / "specs" / "schemas" / "requirements.schema.json", label="requirements")

    d: dict[str, Any]
    if out is None:
        d = {"ok": True, "dry_run": True}
    else:
        try:
            d = out.__dict__  # dataclass
        except Exception:
            d = {"result": str(out)}
        d["ok"] = True
        d["dry_run"] = False

    d.update(
        {
            "scope": scope,
            "project_id": pid,
            "requirements_dir": str(req_dir),
            "raw_inputs_path": str(raw_path),
            "raw_inputs_lines_before": before_raw_lines,
            "raw_inputs_lines_after": after_raw_lines,
        }
    )
    d["_generated_at"] = utc_now_iso()
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
