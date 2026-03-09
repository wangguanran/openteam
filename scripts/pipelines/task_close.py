#!/usr/bin/env python3
from __future__ import annotations

import atexit
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import locks

from _common import (
    PipelineError,
    add_default_args,
    append_jsonl,
    read_json,
    read_text,
    read_yaml,
    resolve_repo_root,
    resolve_workspace_root,
    runtime_state_root,
    utc_now_iso,
    validate_or_die,
    write_yaml,
)


def _find_task_in_dir(tasks_dir: Path, task_id: str) -> Optional[Path]:
    p = tasks_dir / f"{task_id}.yaml"
    return p if p.exists() else None


def _locate_task(repo: Path, ws_root: Path, *, scope: str, task_id: str) -> tuple[str, str, Path, Path]:
    """
    Returns (resolved_scope, project_id, ledger_path, logs_dir).
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        raise PipelineError("task_id is required")

    if scope:
        scope = str(scope).strip()

    runtime_override = "" if str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip() else str(repo.parent / "team-os-runtime")
    state_root = runtime_state_root(override=runtime_override)

    # 1) explicit teamos
    if scope in ("", "teamos"):
        led = _find_task_in_dir(state_root / "ledger" / "tasks", task_id)
        if led:
            logs = state_root / "logs" / "tasks" / task_id
            return ("teamos", "teamos", led, logs)

    # 2) explicit project:<id>
    if scope.startswith("project:"):
        pid = scope.split(":", 1)[1].strip()
        led = _find_task_in_dir(ws_root / "projects" / pid / "state" / "ledger" / "tasks", task_id)
        if not led:
            raise PipelineError(f"task ledger not found for scope={scope}: {task_id}")
        logs = ws_root / "projects" / pid / "state" / "logs" / "tasks" / task_id
        return (scope, pid, led, logs)

    # 3) autodetect in workspace (best-effort, limited)
    projects_dir = ws_root / "projects"
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir():
                continue
            led = _find_task_in_dir(d / "state" / "ledger" / "tasks", task_id)
            if led:
                pid = d.name
                logs = d / "state" / "logs" / "tasks" / task_id
                return (f"project:{pid}", pid, led, logs)

    raise PipelineError(f"task ledger not found: {task_id}")


def _metrics_issues(metrics_path: Path) -> list[str]:
    issues: list[str] = []
    if not metrics_path.exists():
        return [f"missing metrics.jsonl: {metrics_path}"]
    for i, raw in enumerate(read_text(metrics_path).splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                issues.append(f"{metrics_path}:{i} not an object")
                continue
            for k in ("ts", "event_type", "actor"):
                if not str(obj.get(k) or "").strip():
                    issues.append(f"{metrics_path}:{i} missing field: {k}")
        except Exception as e:
            issues.append(f"{metrics_path}:{i} invalid json: {e}")
    return issues


def _has_task_closed_event(metrics_path: Path, *, task_id: str) -> bool:
    if not metrics_path.exists():
        return False
    for raw in read_text(metrics_path).splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if str(obj.get("event_type") or "") == "TASK_CLOSED" and str(obj.get("task_id") or "") == str(task_id):
            return True
    return False


def _run_tests(repo: Path) -> dict[str, Any]:
    # Keep deterministic and offline: stdlib unittest only.
    p = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": (p.stdout or "")[-2000:], "stderr": (p.stderr or "")[-2000:]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Close a task (validate DoD + mark ledger closed)")
    add_default_args(ap)
    ap.add_argument("task_id")
    ap.add_argument("--scope", default="", help="optional: teamos | project:<id> (auto-detect if omitted)")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="validate only; do not write ledger/metrics")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)

    scope, pid, ledger_path, logs_dir = _locate_task(repo, ws_root, scope=str(args.scope or ""), task_id=str(args.task_id))

    # Concurrency: repo lock (teamos only) + scope lock.
    repo_lock = None
    scope_lock = None
    if not args.dry_run:
        if scope == "teamos":
            repo_lock = locks.acquire_repo_lock(repo_root=repo, task_id=str(args.task_id))
        scope_lock = locks.acquire_scope_lock(scope, repo_root=repo, workspace_root=ws_root, req_dir=None, task_id=str(args.task_id))

    def _cleanup_locks() -> None:
        locks.release_lock(scope_lock)
        locks.release_lock(repo_lock)

    atexit.register(_cleanup_locks)

    ledger = read_yaml(ledger_path)
    if not ledger:
        raise PipelineError(f"empty ledger: {ledger_path}")
    validate_or_die(ledger, repo / "specs" / "schemas" / "task_ledger.schema.json", label="task_ledger")

    want_logs = [
        "00_intake.md",
        "01_plan.md",
        "02_todo.md",
        "03_work.md",
        "04_test.md",
        "05_release.md",
        "06_observe.md",
        "07_retro.md",
    ]
    missing_logs = [f for f in want_logs if not (logs_dir / f).exists()]
    metrics_path = logs_dir / "metrics.jsonl"
    metrics_issues = _metrics_issues(metrics_path)

    # Telemetry schema (subset validator). Validate each jsonl line.
    tel_schema = repo / "specs" / "schemas" / "telemetry_event.schema.json"
    if tel_schema.exists() and metrics_path.exists():
        sch = read_json(tel_schema)
        for i, raw in enumerate(read_text(metrics_path).splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            try:
                from _common import validate_schema  # local import to keep file-level imports small

                errs = validate_schema(obj, sch, at=f"metrics[{i}]")
                if errs:
                    metrics_issues.append(f"{metrics_path}:{i} schema_errors={errs[:3]}")
            except Exception:
                pass

    # Policy + purity gates (local, no remote writes).
    policy_ok = True
    policy_failures: list[str] = []
    try:
        sys.path.insert(0, str(repo / "scripts"))
        import policy_check  # type: ignore

        res = policy_check.run_checks(repo_root=repo)  # type: ignore[attr-defined]
        policy_ok = bool(res.ok)
        policy_failures = list(res.failures or [])
    except Exception as e:
        policy_ok = False
        policy_failures = [f"policy_check_failed: {e}"]

    purity_ok = True
    purity_violations: list[dict[str, Any]] = []
    if scope == "teamos":
        try:
            sys.path.insert(0, str(repo / "scripts" / "governance"))
            import check_repo_purity  # type: ignore

            out = check_repo_purity.check_repo_purity(repo)  # type: ignore[attr-defined]
            purity_ok = bool(out.get("ok"))
            purity_violations = list(out.get("violations") or [])
        except Exception as e:
            purity_ok = False
            purity_violations = [{"kind": "CHECK_FAILED", "path": str(repo), "detail": str(e)[:200]}]

    tests = {"ok": True}
    if not args.skip_tests:
        tests = _run_tests(repo)

    ok = (not missing_logs) and (not metrics_issues) and purity_ok and policy_ok and bool(tests.get("ok"))
    now = utc_now_iso()
    actions: list[str] = []

    if ok and (not bool(args.dry_run)):
        # Mark ledger closed (idempotent).
        cur = str(ledger.get("status") or "").strip().lower()
        if cur != "closed":
            ledger["status"] = "closed"
            ledger["updated_at"] = now
            if isinstance(ledger.get("checkpoint"), dict):
                ledger["checkpoint"]["stage"] = "closed"
                ledger["checkpoint"]["last_event_ts"] = now
            actions.append("ledger: status=closed")
            write_yaml(ledger_path, ledger, dry_run=False)

        # Avoid repeated append when close is re-run.
        tid = str(ledger.get("id") or args.task_id)
        if not _has_task_closed_event(metrics_path, task_id=tid):
            append_jsonl(
                metrics_path,
                {
                    "ts": now,
                    "event_type": "TASK_CLOSED",
                    "actor": "pipeline.task_close",
                    "task_id": tid,
                    "project_id": pid,
                    "workstream_id": str(ledger.get("workstream_id") or ""),
                    "severity": "INFO",
                    "message": "task closed (DoD verified)",
                    "payload": {"scope": scope, "ledger_path": str(ledger_path)},
                },
                dry_run=False,
            )
            actions.append("metrics: append TASK_CLOSED")

    out = {
        "ok": ok,
        "scope": scope,
        "project_id": pid,
        "task_id": str(ledger.get("id") or args.task_id),
        "ledger_path": str(ledger_path),
        "logs_dir": str(logs_dir),
        "missing_logs": missing_logs,
        "metrics_issues": metrics_issues[:50],
        "policy": {"ok": policy_ok, "failures": policy_failures[:20]},
        "repo_purity": {"ok": purity_ok, "violations": purity_violations[:20]},
        "tests": {k: v for k, v in (tests or {}).items() if k in ("ok", "returncode", "stdout", "stderr")},
        "actions": actions,
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
