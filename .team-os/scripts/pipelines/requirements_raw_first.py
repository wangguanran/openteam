#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import locks

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

def _load_last_raw_assessment(req_dir: Path, *, raw_id: str) -> dict[str, Any]:
    p = req_dir / "raw_assessments.jsonl"
    if not p.exists():
        return {}
    found: dict[str, Any] = {}
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if str(obj.get("raw_id") or "").strip() == str(raw_id or "").strip():
            found = dict(obj)
    return found


def cmd_add(repo: Path, *, scope: str, workspace_root: str, text: str, workstream_id: str, priority: str, source: str, user: str) -> dict[str, Any]:
    resolved_scope, pid, req_dir = _requirements_dir_for_scope(repo, scope=scope, workspace_root=workspace_root)
    req_dir.mkdir(parents=True, exist_ok=True)
    _add_runtime_template_to_syspath(repo)

    # Unit tests must be offline/fast: disable Codex semantic check (LLM).
    os.environ.setdefault("TEAMOS_REQUIREMENTS_SEMANTIC_CHECK", "0")

    from app.requirements_store import add_requirement_raw_first  # type: ignore

    repo_lock = None
    scope_lock = None
    try:
        if resolved_scope == "teamos":
            repo_lock = locks.acquire_repo_lock(repo_root=repo, task_id=str(os.getenv("TEAMOS_TASK_ID") or ""))
        scope_lock = locks.acquire_scope_lock(
            resolved_scope,
            repo_root=repo,
            workspace_root=Path(workspace_root).expanduser().resolve(),
            req_dir=req_dir,
            task_id=str(os.getenv("TEAMOS_TASK_ID") or ""),
        )

        out = add_requirement_raw_first(
            project_id=pid,
            req_dir=req_dir,
            requirement_text=str(text or "").rstrip(),
            workstream_id=str(workstream_id or "").strip(),
            source=str(source or "cli"),
            channel="cli",
            user=str(user or "unknown"),
            priority=str(priority or "P2"),
            constraints=None,
            acceptance=None,
            rationale="",
        )
    finally:
        locks.release_lock(scope_lock)
        locks.release_lock(repo_lock)

    # Validate the last raw input and expanded requirements.
    raw_last = _load_last_raw_input(req_dir)
    validate_or_die(raw_last, repo / ".team-os" / "schemas" / "requirement_raw_input.schema.json", label="requirement_raw_input")
    raw_id = str(raw_last.get("raw_id") or "").strip()
    if raw_id:
        assess_last = _load_last_raw_assessment(req_dir, raw_id=raw_id)
        validate_or_die(assess_last, repo / ".team-os" / "schemas" / "requirement_raw_assessment.schema.json", label="requirement_raw_assessment")
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


def cmd_migrate_v3(repo: Path, *, scope: str, workspace_root: str, dry_run: bool) -> dict[str, Any]:
    resolved_scope, pid, req_dir = _requirements_dir_for_scope(repo, scope=scope, workspace_root=workspace_root)
    req_dir.mkdir(parents=True, exist_ok=True)
    raw_path = req_dir / "raw_inputs.jsonl"

    repo_lock = None
    scope_lock = None
    try:
        if resolved_scope == "teamos":
            repo_lock = locks.acquire_repo_lock(repo_root=repo, task_id=str(os.getenv("TEAMOS_TASK_ID") or ""))
        scope_lock = locks.acquire_scope_lock(
            resolved_scope,
            repo_root=repo,
            workspace_root=Path(workspace_root).expanduser().resolve(),
            req_dir=req_dir,
            task_id=str(os.getenv("TEAMOS_TASK_ID") or ""),
        )

        # Ensure v3 scaffold files exist (do not touch Expanded artifacts here).
        (req_dir / "feasibility").mkdir(parents=True, exist_ok=True)
        if not (req_dir / "raw_assessments.jsonl").exists() and (not dry_run):
            (req_dir / "raw_assessments.jsonl").write_text("", encoding="utf-8")

        if not raw_path.exists():
            if not dry_run:
                raw_path.write_text("", encoding="utf-8")
            return {"ok": True, "scope": resolved_scope, "project_id": pid, "requirements_dir": str(req_dir), "migrated": False, "reason": "raw_inputs.jsonl missing"}

        original = raw_path.read_text(encoding="utf-8", errors="replace")
        lines = [ln for ln in original.splitlines() if ln.strip()]
        if not lines:
            return {"ok": True, "scope": resolved_scope, "project_id": pid, "requirements_dir": str(req_dir), "migrated": False, "reason": "raw_inputs.jsonl empty"}

        import hashlib

        def sha256_text(s: str) -> str:
            return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

        allowed_channels = {"cli", "api", "chat", "import"}

        def is_system_user(u: str) -> bool:
            uu = (u or "").strip().lower()
            return uu.startswith("system:") or uu in ("self-improve", "self-improve-daemon") or uu.startswith("self-improve")

        migrated_items: list[dict[str, Any]] = []
        skipped = 0
        invalid_json = 0
        for ln in lines:
            try:
                obj = json.loads(ln)
            except Exception:
                invalid_json += 1
                continue
            if not isinstance(obj, dict):
                invalid_json += 1
                continue
            user = str(obj.get("user") or "").strip()
            if is_system_user(user):
                skipped += 1
                continue
            text = str(obj.get("text") or "")
            if not text.strip():
                skipped += 1
                continue
            ch = str(obj.get("channel") or "").strip()
            if ch not in allowed_channels:
                ch = "import"
            ts = str(obj.get("timestamp") or "").strip() or utc_now_iso()
            scope_s = str(obj.get("scope") or "").strip() or resolved_scope
            raw_id = str(obj.get("raw_id") or "").strip()
            if not raw_id:
                raw_id = "RAW-" + sha256_text("|".join([ts, scope_s, user, ch, text]))[:16]
            migrated_items.append(
                {
                    "raw_id": raw_id,
                    "timestamp": ts,
                    "scope": scope_s,
                    "user": user,
                    "channel": ch,
                    "text": text,
                    "text_sha256": sha256_text(text),
                }
            )

        # Deterministic serialization.
        new_lines: list[str] = []
        for it in migrated_items:
            validate_or_die(it, repo / ".team-os" / "schemas" / "requirement_raw_input.schema.json", label="requirement_raw_input")
            new_lines.append(json.dumps(it, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        new_content = ("\n".join(new_lines) + ("\n" if new_lines else ""))

        migrated = new_content != original
        if migrated and (not dry_run):
            # Preserve full legacy file (including system entries) for audit trail.
            ts_compact = utc_now_iso().replace(":", "").replace("-", "")
            legacy = req_dir / f"raw_inputs.v2_legacy_{ts_compact}.jsonl"
            legacy.write_text(original, encoding="utf-8")
            raw_path.write_text(new_content, encoding="utf-8")

        return {
            "ok": True,
            "scope": resolved_scope,
            "project_id": pid,
            "requirements_dir": str(req_dir),
            "migrated": bool(migrated and (not dry_run)),
            "dry_run": bool(dry_run),
            "original_lines": len(lines),
            "user_lines_kept": len(migrated_items),
            "system_or_invalid_skipped": int(skipped + invalid_json),
            "invalid_json": invalid_json,
            "raw_inputs_path": str(raw_path),
        }
    finally:
        locks.release_lock(scope_lock)
        locks.release_lock(repo_lock)


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

    mg = sp.add_parser("migrate-v3", help="One-time migration: enforce Raw-First v3 raw_inputs.jsonl schema (keep user-only; archive legacy)")
    mg.add_argument("--scope", required=True)
    mg.add_argument("--dry-run", action="store_true")

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
    elif args.cmd == "rebuild":
        out = cmd_rebuild(repo, scope=str(args.scope), workspace_root=ws)
    else:
        out = cmd_migrate_v3(repo, scope=str(args.scope), workspace_root=ws, dry_run=bool(getattr(args, "dry_run", False)))

    out["_generated_at"] = utc_now_iso()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if bool(out.get("ok", True)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
