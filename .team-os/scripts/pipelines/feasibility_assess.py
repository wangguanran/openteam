#!/usr/bin/env python3
"""
Deterministic feasibility assessment runner.

Writes (scope-specific):
- <req_dir>/feasibility/<raw_id>.md
- <req_dir>/raw_assessments.jsonl (append-only index)

Notes:
- This is used by the Raw-First v3 requirements flow.
- Raw inputs are never modified by this script.
"""

from __future__ import annotations

import argparse
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, append_jsonl, read_text, resolve_repo_root, resolve_workspace_root, utc_now_iso, validate_or_die, write_text


def _add_runtime_template_to_syspath(repo: Path) -> None:
    import sys

    app_dir = repo / ".team-os" / "templates" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


def _requirements_dir_for_scope(repo: Path, *, scope: str, workspace_root: Path) -> tuple[str, str, Path]:
    s = str(scope or "").strip()
    if not s:
        raise PipelineError("missing --scope teamos|project:<id>")
    if s == "teamos":
        return ("teamos", "teamos", repo / "docs" / "teamos" / "requirements")
    if s.startswith("project:"):
        pid = s.split(":", 1)[1].strip()
        if not pid:
            raise PipelineError("invalid scope: project:<id> missing id")
        from _common import is_within, safe_project_id

        pid = safe_project_id(pid)
        if is_within(workspace_root, repo):
            raise PipelineError(f"invalid workspace_root={workspace_root} (must be outside repo={repo})")
        return (s, pid, workspace_root / "projects" / pid / "state" / "requirements")
    # backward compatible bare id
    return _requirements_dir_for_scope(repo, scope=f"project:{s}", workspace_root=workspace_root)


def _sha256_text(text: str) -> str:
    return sha256((text or "").encode("utf-8")).hexdigest()


def _find_existing_assessment(assess_path: Path, *, raw_id: str) -> dict[str, Any] | None:
    if not assess_path.exists():
        return None
    for ln in read_text(assess_path).splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if str(obj.get("raw_id") or "") == raw_id:
            found = dict(obj)
    return found if "found" in locals() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Feasibility assessment runner (deterministic)")
    add_default_args(ap)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--raw-id", required=True)
    ap.add_argument("--timestamp", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--channel", default="cli")
    ap.add_argument("--text", required=True)
    ap.add_argument("--assessor", default="deterministic.rules.v1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    scope, pid, req_dir = _requirements_dir_for_scope(repo, scope=str(args.scope), workspace_root=ws_root)
    req_dir.mkdir(parents=True, exist_ok=True)

    raw_id = str(args.raw_id or "").strip()
    if not raw_id:
        raise PipelineError("--raw-id is required")

    ts = str(args.timestamp or "").strip() or utc_now_iso()
    raw: dict[str, Any] = {
        "raw_id": raw_id,
        "timestamp": ts,
        "scope": scope,
        "user": str(args.user or "").strip(),
        "channel": str(args.channel or "").strip(),
        "text": str(args.text or ""),
        "text_sha256": _sha256_text(str(args.text or "")),
    }

    _add_runtime_template_to_syspath(repo)
    from app import feasibility  # type: ignore

    assessment = feasibility.assess(scope=scope, text=str(args.text or ""))  # type: ignore[attr-defined]
    report_md = feasibility.render_report(raw=raw, assessment=assessment)  # type: ignore[attr-defined]
    report_sha = _sha256_text(report_md)

    feas_dir = req_dir / "feasibility"
    report_path = feas_dir / f"{raw_id}.md"
    feas_dir.mkdir(parents=True, exist_ok=True)

    # Portable reference from requirements dir.
    try:
        rel_report = os.path.relpath(str(report_path), start=str(req_dir))
    except Exception:
        rel_report = str(report_path)

    assess_idx_path = req_dir / "raw_assessments.jsonl"
    record = {
        "raw_id": raw_id,
        "assessed_at": utc_now_iso(),
        "outcome": assessment.outcome,
        "report_path": rel_report,
        "assessor": str(args.assessor or "").strip(),
        "report_sha256": report_sha,
    }

    validate_or_die(record, repo / ".team-os" / "schemas" / "requirement_raw_assessment.schema.json", label="requirement_raw_assessment")

    existing = _find_existing_assessment(assess_idx_path, raw_id=raw_id)
    would_write = not (existing and str(existing.get("report_sha256") or "") == report_sha and str(existing.get("outcome") or "") == assessment.outcome)

    if not args.dry_run and would_write:
        # Write report (stable content).
        write_text(report_path, report_md, dry_run=False)
        # Append index record (append-only).
        append_jsonl(assess_idx_path, record, dry_run=False)

    out = {
        "ok": True,
        "scope": scope,
        "project_id": pid,
        "raw_id": raw_id,
        "outcome": assessment.outcome,
        "report_path": str(report_path),
        "report_rel_path": rel_report,
        "assessment_index_path": str(assess_idx_path),
        "wrote": bool((not args.dry_run) and would_write),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

