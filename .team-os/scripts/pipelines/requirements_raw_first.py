#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, resolve_repo_root, utc_now_iso, validate_or_die


def _add_runtime_template_to_syspath(repo: Path) -> None:
    app_dir = repo / ".team-os" / "templates" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


def _requirements_dir_for_scope(repo: Path, *, scope: str, workspace_root: str) -> tuple[str, str, Path]:
    """
    Returns (resolved_scope, project_id, req_dir).
    """
    s = str(scope or "").strip()
    if not s:
        raise PipelineError("missing --scope teamos|project:<id>")
    if s == "teamos":
        return ("teamos", "teamos", repo / "docs" / "teamos" / "requirements")
    if s.startswith("project:"):
        pid = s.split(":", 1)[1].strip()
        if not pid:
            raise PipelineError("invalid scope: project:<id> missing id")
        ws = Path(workspace_root).expanduser().resolve()
        from _common import is_within, safe_project_id

        pid = safe_project_id(pid)
        if is_within(ws, repo):
            raise PipelineError(f"invalid workspace_root={ws} (must be outside repo={repo})")
        return (s, pid, ws / "projects" / pid / "state" / "requirements")
    # backward compat: bare id
    return _requirements_dir_for_scope(repo, scope=f"project:{s}", workspace_root=workspace_root)


def _load_last_raw_input(req_dir: Path) -> dict[str, Any]:
    p = req_dir / "raw_inputs.jsonl"
    if not p.exists():
        return {}
    lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    if not lines:
        return {}
    try:
        obj = json.loads(lines[-1])
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def cmd_add(repo: Path, *, scope: str, workspace_root: str, text: str, workstream_id: str, priority: str, source: str, user: str) -> dict[str, Any]:
    resolved_scope, pid, req_dir = _requirements_dir_for_scope(repo, scope=scope, workspace_root=workspace_root)
    req_dir.mkdir(parents=True, exist_ok=True)
    _add_runtime_template_to_syspath(repo)

    # Unit tests must be offline/fast: disable Codex semantic check (LLM).
    os.environ.setdefault("TEAMOS_REQUIREMENTS_SEMANTIC_CHECK", "0")

    from app.requirements_store import add_requirement_raw_first  # type: ignore

    out = add_requirement_raw_first(
        project_id=pid,
        req_dir=req_dir,
        requirement_text=str(text or "").rstrip(),
        source=str(source or "cli"),
        channel="cli",
        user=str(user or "unknown"),
        priority=str(priority or "P2"),
        workstreams=[workstream_id] if workstream_id else None,
        constraints=None,
        acceptance=None,
        rationale="",
    )

    # Validate the last raw input and expanded requirements.
    raw_last = _load_last_raw_input(req_dir)
    validate_or_die(raw_last, repo / ".team-os" / "schemas" / "requirement_raw_input.schema.json", label="requirement_raw_input")
    y = req_dir / "requirements.yaml"
    if y.exists():
        import yaml

        data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
        validate_or_die(data, repo / ".team-os" / "schemas" / "requirements.schema.json", label="requirements")

    # Normalize output for JSON.
    try:
        d = out.__dict__  # dataclass
    except Exception:
        d = {"result": str(out)}
    d.update({"scope": resolved_scope, "project_id": pid, "requirements_dir": str(req_dir)})
    return d


def cmd_verify(repo: Path, *, scope: str, workspace_root: str) -> dict[str, Any]:
    resolved_scope, pid, req_dir = _requirements_dir_for_scope(repo, scope=scope, workspace_root=workspace_root)
    _add_runtime_template_to_syspath(repo)
    from app.requirements_store import verify_requirements_raw_first  # type: ignore

    out = verify_requirements_raw_first(req_dir, project_id=pid)
    out["scope"] = resolved_scope
    return out


def cmd_rebuild(repo: Path, *, scope: str, workspace_root: str) -> dict[str, Any]:
    resolved_scope, pid, req_dir = _requirements_dir_for_scope(repo, scope=scope, workspace_root=workspace_root)
    _add_runtime_template_to_syspath(repo)
    from app.requirements_store import rebuild_requirements_md  # type: ignore

    out = rebuild_requirements_md(req_dir, project_id=pid)
    out["scope"] = resolved_scope
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Requirements Raw-First pipeline (capture -> baseline -> drift -> conflict -> expand -> render -> changelog)")
    add_default_args(ap)
    sp = ap.add_subparsers(dest="cmd", required=True)

    add = sp.add_parser("add", help="Add a raw requirement and update Expanded artifacts")
    add.add_argument("--scope", required=True)
    add.add_argument("--text", required=True)
    add.add_argument("--workstream", default="general")
    add.add_argument("--priority", default="P2")
    add.add_argument("--source", default="cli")
    add.add_argument("--user", default=os.getenv("USER") or "user")

    vr = sp.add_parser("verify", help="Check-only drift/conflicts (no writes)")
    vr.add_argument("--scope", required=True)

    rb = sp.add_parser("rebuild", help="Deterministic rebuild REQUIREMENTS.md from requirements.yaml")
    rb.add_argument("--scope", required=True)

    args = ap.parse_args(argv)
    repo = resolve_repo_root(args)
    ws = str(getattr(args, "workspace_root", "") or os.getenv("TEAMOS_WORKSPACE_ROOT") or (Path.home() / ".teamos" / "workspace"))

    if args.cmd == "add":
        out = cmd_add(
            repo,
            scope=str(args.scope),
            workspace_root=ws,
            text=str(args.text),
            workstream_id=str(args.workstream or "general"),
            priority=str(args.priority or "P2"),
            source=str(args.source or "cli"),
            user=str(args.user or "user"),
        )
    elif args.cmd == "verify":
        out = cmd_verify(repo, scope=str(args.scope), workspace_root=ws)
    else:
        out = cmd_rebuild(repo, scope=str(args.scope), workspace_root=ws)

    out["_generated_at"] = utc_now_iso()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if bool(out.get("ok", True)) else 2


if __name__ == "__main__":
    raise SystemExit(main())

