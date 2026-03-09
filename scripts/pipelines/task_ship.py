#!/usr/bin/env python3
"""
Task ship pipeline (close -> gates -> commit -> push -> (optional) PR).

Governance:
- A Task is the update unit. Shipping is only allowed after `task close` passes.
- If push fails (or origin is missing), mark the task as BLOCKED and record the reason.
- Push gates: repo purity, policy check, tests, and a lightweight secrets scan.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from _common import (
    PipelineError,
    add_default_args,
    append_jsonl,
    append_text,
    read_text,
    read_yaml,
    resolve_repo_root,
    resolve_workspace_root,
    runtime_state_root,
    utc_now_iso,
    validate_or_die,
    write_yaml,
)


def _run(cmd: list[str], *, cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if check and p.returncode != 0:
        raise PipelineError(f"command_failed rc={p.returncode} cmd={' '.join(cmd)} stderr={(p.stderr or '').strip()[:300]}")
    return p


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

    scope = str(scope or "").strip()
    runtime_override = "" if str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip() else str(repo.parent / "team-os-runtime")
    state_root = runtime_state_root(override=runtime_override)

    # 1) teamos
    if scope in ("", "teamos"):
        led = _find_task_in_dir(state_root / "ledger" / "tasks", task_id)
        if led:
            logs = state_root / "logs" / "tasks" / task_id
            return ("teamos", "teamos", led, logs)

    # 2) explicit project:<id> (best-effort)
    if scope.startswith("project:"):
        pid = scope.split(":", 1)[1].strip()
        led = _find_task_in_dir(ws_root / "projects" / pid / "state" / "ledger" / "tasks", task_id)
        if not led:
            raise PipelineError(f"task ledger not found for scope={scope}: {task_id}")
        logs = ws_root / "projects" / pid / "state" / "logs" / "tasks" / task_id
        return (scope, pid, led, logs)

    raise PipelineError(f"task ledger not found: {task_id}")


def _current_branch(repo: Path) -> str:
    p = _run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    if p.returncode != 0:
        raise PipelineError(f"git branch detect failed: {(p.stderr or '').strip()[:200]}")
    return (p.stdout or "").strip()


def _origin_url(repo: Path) -> str:
    p = _run(["git", "-C", str(repo), "remote", "get-url", "origin"], cwd=repo)
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def _git_clean(repo: Path) -> bool:
    p = _run(["git", "-C", str(repo), "status", "--porcelain"], cwd=repo)
    return (p.returncode == 0) and (not (p.stdout or "").strip())


_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OPENAI_API_KEY", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("GITHUB_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY-----")),
]


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]


def _secrets_scan(repo: Path) -> list[dict[str, Any]]:
    """
    Best-effort secrets scan on changed/untracked files (local-only).
    Returns findings list; empty means PASS.
    """
    p = _run(["git", "-C", str(repo), "ls-files", "-m", "-o", "--exclude-standard"], cwd=repo)
    if p.returncode != 0:
        return [{"kind": "SCAN_FAILED", "path": str(repo), "detail": (p.stderr or "").strip()[:200]}]
    paths = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
    findings: list[dict[str, Any]] = []
    for rel in sorted(set(paths)):
        fp = (repo / rel).resolve()
        if not fp.exists() or fp.is_dir():
            continue
        try:
            data = fp.read_bytes()
        except Exception:
            continue
        if _looks_binary(data):
            continue
        text = data.decode("utf-8", errors="replace")
        for name, pat in _SECRET_PATTERNS:
            m = pat.search(text)
            if not m:
                continue
            findings.append({"kind": "SECRET_PATTERN", "pattern": name, "path": rel, "sample": m.group(0)[:12] + "..."} )
    return findings[:50]


def _mark_blocked(
    *,
    repo: Path,
    ledger_path: Path,
    logs_dir: Path,
    metrics_path: Path,
    task_id: str,
    project_id: str,
    workstream_id: str,
    reason: str,
    detail: str,
) -> None:
    now = utc_now_iso()
    ledger = read_yaml(ledger_path)
    validate_or_die(ledger, repo / "specs" / "schemas" / "task_ledger.schema.json", label="task_ledger")
    ledger["status"] = "blocked"
    ledger["updated_at"] = now
    ledger.setdefault("blockers", [])
    if not isinstance(ledger.get("blockers"), list):
        ledger["blockers"] = []
    ledger["blockers"].append({"ts": now, "reason": reason, "detail": str(detail or "")[:500]})
    if isinstance(ledger.get("checkpoint"), dict):
        ledger["checkpoint"]["stage"] = "blocked"
        ledger["checkpoint"]["last_event_ts"] = now
    write_yaml(ledger_path, ledger, dry_run=False)

    append_jsonl(
        metrics_path,
        {
            "ts": now,
            "event_type": "TASK_BLOCKED",
            "actor": "pipeline.task_ship",
            "task_id": task_id,
            "project_id": project_id,
            "workstream_id": workstream_id,
            "severity": "ERROR",
            "message": f"ship blocked: {reason}",
            "payload": {"detail": str(detail or "")[:800]},
        },
        dry_run=False,
    )
    # Record in 03_work.md (append-only).
    work_log = logs_dir / "03_work.md"
    append_text(
        work_log,
        "\n".join(
            [
                "",
                f"## Ship Blocked ({now})",
                "",
                f"- reason: {reason}",
                f"- detail: {str(detail or '').strip()[:800]}",
                "",
            ]
        ),
        dry_run=False,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ship a task (close -> gates -> commit -> push)")
    add_default_args(ap)
    ap.add_argument("task_id")
    ap.add_argument("--scope", default="teamos", help="teamos | project:<id> (default: teamos)")
    ap.add_argument("--summary", default="", help="commit summary (default: ledger title)")
    ap.add_argument("--base", default="main", help="PR base branch (gh only; default: main)")
    ap.add_argument("--no-pr", action="store_true", help="do not create PR")
    ap.add_argument("--dry-run", action="store_true", help="plan only; do not commit/push (still runs task close)")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    scope, pid, ledger_path, logs_dir = _locate_task(repo, ws_root, scope=str(args.scope or ""), task_id=str(args.task_id))
    ledger = read_yaml(ledger_path)
    validate_or_die(ledger, repo / "specs" / "schemas" / "task_ledger.schema.json", label="task_ledger")
    task_id = str(ledger.get("id") or args.task_id).strip()
    workstream_id = str(ledger.get("workstream_id") or "")
    metrics_path = logs_dir / "metrics.jsonl"

    branch = _current_branch(repo)
    # Branch policy: no longer enforce per-task temp branches.
    # - Default workflow can ship directly from `main` after `task close` passes.
    # - PR creation is skipped when head == base.

    # 1) Close gate (must pass)
    close_script = repo / "scripts" / "pipelines" / "task_close.py"
    close_argv = [
        sys.executable,
        str(close_script),
        "--repo-root",
        str(repo),
        "--workspace-root",
        str(ws_root),
        task_id,
        "--scope",
        scope,
    ]
    pc = subprocess.run(close_argv, cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    sys.stdout.write((pc.stdout or ""))
    if pc.returncode != 0:
        sys.stderr.write((pc.stderr or ""))
        raise SystemExit(pc.returncode)

    # 2) Secrets scan (before commit/push)
    secrets = _secrets_scan(repo)
    if secrets:
        _mark_blocked(
            repo=repo,
            ledger_path=ledger_path,
            logs_dir=logs_dir,
            metrics_path=metrics_path,
            task_id=task_id,
            project_id=pid,
            workstream_id=workstream_id,
            reason="SECRETS_DETECTED",
            detail=json.dumps(secrets, ensure_ascii=False)[:800],
        )
        print(json.dumps({"ok": False, "reason": "SECRETS_DETECTED", "findings": secrets}, ensure_ascii=False, indent=2))
        return 2

    summary = str(args.summary or "").strip() or str(ledger.get("title") or "").strip()
    summary = summary.replace("\n", " ").strip()
    if not summary:
        summary = "ship"
    msg = f"{task_id}: {summary}"

    if args.dry_run:
        origin = _origin_url(repo)
        print(json.dumps({"ok": True, "dry_run": True, "branch": branch, "origin": origin, "commit_message": msg}, ensure_ascii=False, indent=2))
        return 0

    # Preflight push (auth/connectivity). Mark BLOCKED and still allow a local commit.
    origin = _origin_url(repo)
    push_precheck_ok = bool(origin)
    push_precheck_detail = ""
    if origin:
        pre = _run(["git", "-C", str(repo), "push", "--dry-run", "origin", f"HEAD:refs/heads/{branch}"], cwd=repo)
        push_precheck_ok = pre.returncode == 0
        push_precheck_detail = (pre.stderr or pre.stdout or "").strip()[:800]

    if not origin:
        _mark_blocked(
            repo=repo,
            ledger_path=ledger_path,
            logs_dir=logs_dir,
            metrics_path=metrics_path,
            task_id=task_id,
            project_id=pid,
            workstream_id=workstream_id,
            reason="NO_REMOTE",
            detail="git remote 'origin' is missing; configure origin then re-run task ship",
        )
    elif not push_precheck_ok:
        _mark_blocked(
            repo=repo,
            ledger_path=ledger_path,
            logs_dir=logs_dir,
            metrics_path=metrics_path,
            task_id=task_id,
            project_id=pid,
            workstream_id=workstream_id,
            reason="PUSH_PRECHECK_FAILED",
            detail=push_precheck_detail or "git push --dry-run failed",
        )

    # 3) Commit (includes BLOCKED updates if present)
    _run(["git", "-C", str(repo), "add", "-A"], cwd=repo, check=True)

    # No-op guard
    if _git_clean(repo):
        print("nothing_to_ship: working tree clean")
        return 0

    p = _run(["git", "-C", str(repo), "commit", "-m", msg], cwd=repo)
    if p.returncode != 0:
        raise PipelineError(f"git commit failed: {(p.stderr or p.stdout or '').strip()[:300]}")
    sha = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], cwd=repo, check=True)
    commit_sha = (sha.stdout or "").strip()

    # If push is known-broken, stop here (task is BLOCKED, but commit exists).
    if (not origin) or (not push_precheck_ok):
        print(json.dumps({"ok": False, "reason": "PUSH_BLOCKED", "origin": origin, "commit": commit_sha, "detail": push_precheck_detail}, ensure_ascii=False, indent=2))
        return 2

    # 4) Push
    push = _run(["git", "-C", str(repo), "push", "-u", "origin", branch], cwd=repo)
    if push.returncode != 0:
        _mark_blocked(
            repo=repo,
            ledger_path=ledger_path,
            logs_dir=logs_dir,
            metrics_path=metrics_path,
            task_id=task_id,
            project_id=pid,
            workstream_id=workstream_id,
            reason="PUSH_FAILED",
            detail=(push.stderr or push.stdout or "").strip()[:800],
        )
        print(json.dumps({"ok": False, "reason": "PUSH_FAILED", "stderr": (push.stderr or "")[-800:]}, ensure_ascii=False, indent=2))
        return 2

    # 5) Optional PR (skip when head == base)
    pr_url = ""
    if (not bool(args.no_pr)) and (str(branch).strip() != str(args.base or "main").strip()):
        gh = _run(["gh", "auth", "status", "-h", "github.com"], cwd=repo)
        if gh.returncode == 0:
            # If PR already exists, use it; otherwise create.
            view = _run(["gh", "pr", "view", "--json", "url", "--jq", ".url"], cwd=repo)
            if view.returncode == 0 and (view.stdout or "").strip():
                pr_url = (view.stdout or "").strip()
            else:
                body = "\n".join(
                    [
                        f"Task: {task_id}",
                        "",
                        "Acceptance:",
                        f"- ./teamos task close {task_id} --scope {scope}",
                        "- ./teamos policy check",
                        "- python3 -m unittest -q",
                        "- ./teamos doctor",
                        "",
                        f"Ship: {branch}",
                    ]
                )
                pr = _run(["gh", "pr", "create", "--title", msg, "--body", body, "--base", str(args.base), "--head", branch], cwd=repo)
                if pr.returncode == 0:
                    pr_url = (pr.stdout or "").strip().splitlines()[-1].strip() if (pr.stdout or "").strip() else ""

    print(json.dumps({"ok": True, "branch": branch, "commit": commit_sha, "pr": pr_url}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
