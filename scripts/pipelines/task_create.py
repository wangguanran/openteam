#!/usr/bin/env python3
from __future__ import annotations

import atexit
import argparse
import json
import os
from pathlib import Path
from typing import Any

import locks

from _common import (
    PipelineError,
    add_default_args,
    append_jsonl,
    default_runtime_root,
    ensure_dir,
    parse_scope,
    read_text,
    render_template,
    resolve_repo_root,
    resolve_workspace_root,
    runtime_state_root,
    utc_now_iso,
    validate_or_die,
    write_text,
    write_yaml,
)


def _next_teamos_seq(tasks_dir: Path) -> int:
    import re

    pat = re.compile(r"^TEAMOS-(\d{4})$")
    mx = 0
    for p in sorted(tasks_dir.glob("*.yaml")):
        stem = p.stem
        m = pat.match(stem)
        if not m:
            continue
        try:
            mx = max(mx, int(m.group(1)))
        except Exception:
            continue
    return mx + 1


def _generate_task_id(*, scope: str, project_id: str, tasks_dir: Path, logs_root: Path) -> str:
    if scope == "teamos":
        seq = _next_teamos_seq(tasks_dir)
        return f"TEAMOS-{seq:04d}"
    # project scope: keep local-only deterministic id in its own ledger dir
    import re

    pat = re.compile(rf"^{re.escape(project_id.upper())}-(\d{{4}})$")
    mx = 0
    for p in sorted(tasks_dir.glob(f"{project_id.upper()}-*.yaml")):
        m = pat.match(p.stem)
        if not m:
            continue
        try:
            mx = max(mx, int(m.group(1)))
        except Exception:
            continue
    for p in sorted(logs_root.iterdir()) if logs_root.exists() else []:
        if not p.is_dir():
            continue
        m = pat.match(p.name)
        if not m:
            continue
        try:
            mx = max(mx, int(m.group(1)))
        except Exception:
            continue
    return f"{project_id.upper()}-{mx + 1:04d}"


def _task_paths(*, repo: Path, ws_root: Path, scope: str, project_id: str) -> tuple[Path, Path]:
    """
    Returns (tasks_dir, logs_root).
    """
    if scope == "teamos":
        runtime_override = "" if str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip() else str(default_runtime_root())
        state_root = runtime_state_root(override=runtime_override)
        return (state_root / "ledger" / "tasks", state_root / "logs" / "tasks")

    # project scope must live in Workspace (outside repo)
    from _common import is_within

    if is_within(ws_root, repo):
        raise PipelineError(f"invalid workspace_root={ws_root} (must be outside repo={repo})")

    base = ws_root / "projects" / project_id / "state"
    return (base / "ledger" / "tasks", base / "logs" / "tasks")


def _render_log_from_tpl(repo: Path, *, name: str, task_id: str, title: str) -> str:
    tpl = repo / "templates" / "tasks" / f"task_log_{name}"
    if not tpl.exists():
        raise PipelineError(f"missing template: {tpl}")
    date = utc_now_iso().split("T", 1)[0]
    return render_template(
        read_text(tpl),
        {
            "TASK_ID": task_id,
            "TITLE": title,
            "DATE": date,
        },
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create a Team OS task (deterministic scaffold)")
    add_default_args(ap)
    ap.add_argument("--scope", required=True, help="teamos | project:<id>")
    ap.add_argument("--title", required=True)
    ap.add_argument("--workstreams", default="general", help="comma-separated; first item used as workstream_id")
    ap.add_argument("--risk-level", default="R1", help="R0|R1|R2|R3")
    ap.add_argument("--mode", default="auto", help="auto|bootstrap|upgrade")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    scope, pid = parse_scope(args.scope)

    # Concurrency: repo lock (teamos only) + scope lock.
    repo_lock = None
    scope_lock = None
    if not args.dry_run:
        if scope == "teamos":
            repo_lock = locks.acquire_repo_lock(repo_root=repo, task_id=str(os.getenv("TEAMOS_TASK_ID") or ""))
        scope_lock = locks.acquire_scope_lock(scope, repo_root=repo, workspace_root=ws_root, req_dir=None, task_id=str(os.getenv("TEAMOS_TASK_ID") or ""))

    def _cleanup_locks() -> None:
        locks.release_lock(scope_lock)
        locks.release_lock(repo_lock)

    atexit.register(_cleanup_locks)

    workstreams = [x.strip() for x in str(args.workstreams or "").split(",") if x.strip()]
    workstream_id = workstreams[0] if workstreams else "general"

    tasks_dir, logs_root = _task_paths(repo=repo, ws_root=ws_root, scope=scope, project_id=pid)
    ensure_dir(tasks_dir, dry_run=bool(args.dry_run))
    ensure_dir(logs_root, dry_run=bool(args.dry_run))

    task_id = _generate_task_id(scope=scope, project_id=pid, tasks_dir=tasks_dir, logs_root=logs_root)
    ledger_path = tasks_dir / f"{task_id}.yaml"
    if ledger_path.exists():
        raise PipelineError(f"refusing to overwrite ledger: {ledger_path}")
    logs_dir = logs_root / task_id
    recovering_existing_scaffold = False
    if logs_dir.exists() and any(logs_dir.iterdir()):
        recovering_existing_scaffold = True

    now = utc_now_iso()

    def _artifact_path(p: Path) -> str:
        try:
            return str(p.relative_to(repo))
        except Exception:
            return str(p)

    ledger: dict[str, Any] = {
        "id": task_id,
        "title": str(args.title),
        "project_id": pid,
        "workstream_id": workstream_id,
        "workstreams": workstreams or None,
        "status": "intake",
        "risk_level": str(args.risk_level or "R1"),
        "need_pm_decision": False,
        "repo": {"locator": "", "workdir": "", "branch": "", "mode": str(args.mode or "auto")},
        "checkpoint": {"stage": "intake", "last_event_ts": now},
        "recovery": {"last_scan_at": "", "last_resume_at": "", "notes": ""},
        "approvals_required": ["R2/R3 actions"],
        "owners": ["PM-Intake"],
        "roles_involved": ["PM-Intake"],
        "orchestration": {"engine": "crewai", "flow": "genesis"},
        "workflows": ["Genesis"],
        "created_at": now,
        "updated_at": now,
        "links": {"pr": "", "issue": ""},
        "artifacts": {"ledger": _artifact_path(ledger_path), "logs_dir": _artifact_path(logs_dir)},
        "evidence": [{"type": "log", "path": _artifact_path(logs_dir / "00_intake.md")}],
    }

    # Schema validation (best-effort; task ledger lives in YAML but schema validates the loaded dict).
    validate_or_die(ledger, repo / "specs" / "schemas" / "task_ledger.schema.json", label="task_ledger")

    if not args.dry_run:
        ensure_dir(logs_dir, dry_run=False)
        write_yaml(ledger_path, ledger, dry_run=False)

        for name in [
            "00_intake.md",
            "01_plan.md",
            "02_todo.md",
            "03_work.md",
            "04_test.md",
            "05_release.md",
            "06_observe.md",
            "07_retro.md",
        ]:
            out = logs_dir / name
            if out.exists():
                continue
            txt = _render_log_from_tpl(repo, name=name, task_id=task_id, title=str(args.title))
            write_text(out, txt, dry_run=False)

        metrics = logs_dir / "metrics.jsonl"
        append_jsonl(
            metrics,
            {
                "ts": now,
                "event_type": "TASK_RECOVERED" if recovering_existing_scaffold else "TASK_CREATED",
                "actor": "pipeline.task_create",
                "task_id": task_id,
                "project_id": pid,
                "workstream_id": workstream_id,
                "severity": "INFO",
                "message": "task scaffold recovered" if recovering_existing_scaffold else "task scaffold created",
                "payload": {"ledger": str(ledger_path), "logs_dir": str(logs_dir), "scope": scope},
            },
            dry_run=False,
        )

    out = {"ok": True, "scope": scope, "project_id": pid, "task_id": task_id, "ledger_path": str(ledger_path), "logs_dir": str(logs_dir), "dry_run": bool(args.dry_run)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
