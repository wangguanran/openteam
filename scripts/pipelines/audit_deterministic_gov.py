#!/usr/bin/env python3
"""
Deterministic governance/compliance audit report generator (scope=teamos).

Writes:
- docs/audits/DETERMINISTIC_GOV_AUDIT_<ts>.md

Design:
- Deterministic + offline-first: uses local checks; GitHub PR links are best-effort via `gh`.
- Does NOT mutate truth sources beyond writing the audit report itself.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, resolve_repo_root, resolve_workspace_root, utc_now_iso


def _run(cmd: list[str], *, cwd: Path, timeout_sec: int = 300, env: dict[str, str] | None = None) -> dict[str, Any]:
    e = dict(os.environ)
    if env:
        e.update({k: str(v) for k, v in env.items()})
    p = subprocess.run(cmd, cwd=str(cwd), env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec, check=False)
    return {
        "cmd": " ".join(cmd),
        "returncode": p.returncode,
        "stdout": (p.stdout or "").strip(),
        "stderr": (p.stderr or "").strip(),
    }


def _tail(text: str, *, max_lines: int = 30, max_chars: int = 3000) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    lines = s.splitlines()[-max_lines:]
    out = "\n".join(lines)
    return out[-max_chars:]


def _git_rev(repo: Path, ref: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), "rev-parse", ref], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def _gh_pr_url(repo: Path, head_branch: str) -> str:
    if shutil.which("gh") is None:
        return ""
    # Best-effort: list PRs for head branch.
    p = subprocess.run(
        ["gh", "pr", "list", "--head", head_branch, "--json", "url", "--jq", ".[0].url"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def _task_rows(repo: Path) -> list[dict[str, str]]:
    # Hardcode the required tasks for this audit.
    tasks = [
        {"task_id": "TASK-20260216-233035", "title": "TEAMOS-SCRIPT-PIPELINES", "branch": "teamos/TASK-20260216-233035-script-pipelines"},
        {"task_id": "TEAMOS-0001", "title": "TEAMOS-AGENTS-MANUAL", "branch": "teamos/TEAMOS-0001-agents-manual"},
        {"task_id": "TEAMOS-0002", "title": "TEAMOS-ALWAYS-ON-SELF-IMPROVE", "branch": "teamos/TEAMOS-0002-always-on-self-improve"},
        {"task_id": "TEAMOS-0003", "title": "TEAMOS-GIT-PUSH-DISCIPLINE", "branch": "teamos/TEAMOS-0003-git-push-discipline"},
        {"task_id": "TEAMOS-0004", "title": "DETERMINISTIC-GOV-AUDIT", "branch": "teamos/TEAMOS-0004-deterministic-gov-audit"},
        {"task_id": "TEAMOS-0005", "title": "TEAMOS-PROJECT-AGENTS-MANUAL", "branch": "teamos/TEAMOS-0005-project-agents-manual"},
        {"task_id": "TEAMOS-0006", "title": "DETERMINISTIC-GOV-AUDIT-v2", "branch": "teamos/TEAMOS-0006-deterministic-gov-audit-v2"},
        {"task_id": "TEAMOS-0007", "title": "TEAMOS-AUDIT-0001", "branch": "teamos/TEAMOS-0007-execution-strategy-audit"},
        {"task_id": "TEAMOS-0008", "title": "TEAMOS-APPROVALS-DB", "branch": "teamos/TEAMOS-0008-approvals-db"},
        {"task_id": "TEAMOS-0009", "title": "TEAMOS-CENTRAL-MODEL-ALLOWLIST", "branch": "teamos/TEAMOS-0009-central-model-allowlist"},
        {"task_id": "TEAMOS-0010", "title": "TEAMOS-RECOVERY", "branch": "teamos/TEAMOS-0010-recovery"},
        {"task_id": "TEAMOS-0011", "title": "TEAMOS-ALWAYS-ON", "branch": "teamos/TEAMOS-0011-always-on"},
        {"task_id": "TEAMOS-0012", "title": "TEAMOS-PROJECTS-SYNC", "branch": "teamos/TEAMOS-0012-projects-sync"},
        {"task_id": "TEAMOS-0013", "title": "TEAMOS-VERIFY-0001", "branch": "teamos/TEAMOS-0013-verify"},
    ]
    out: list[dict[str, str]] = []
    for t in tasks:
        br = t["branch"]
        sha = _git_rev(repo, br) or _git_rev(repo, f"origin/{br}")
        pr = _gh_pr_url(repo, br)
        out.append({"task_id": t["task_id"], "title": t["title"], "branch": br, "commit": sha[:12] if sha else "", "pr": pr})
    return out


def _md_report(
    *,
    ts: str,
    repo: Path,
    ws_root: Path,
    checks: dict[str, dict[str, Any]],
    tasks: list[dict[str, str]],
    controls: list[dict[str, Any]],
) -> str:
    head = _git_rev(repo, "HEAD")[:12]
    lines: list[str] = []
    lines.append(f"# Deterministic Governance Audit ({ts})")
    lines.append("")
    lines.append("## Context")
    lines.append("")
    lines.append(f"- repo: {repo}")
    lines.append(f"- workspace_root: {ws_root}")
    lines.append(f"- git_sha: {head}")
    lines.append("")
    lines.append("## Task Evidence (Update Units)")
    lines.append("")
    for t in tasks:
        lines.append(f"- {t.get('task_id')} {t.get('title')}")
        lines.append(f"  - branch: {t.get('branch')}")
        lines.append(f"  - commit: {t.get('commit')}")
        lines.append(f"  - pr: {t.get('pr') or '(n/a)'}")
    lines.append("")
    lines.append("## Controls (PASS/FAIL/WAIVED)")
    lines.append("")
    for c in controls:
        lines.append(f"- {c.get('name')}: {c.get('status')}  ({c.get('note')})")
        if c.get("evidence"):
            lines.append(f"  - evidence: {c.get('evidence')}")
    lines.append("")
    lines.append("## Evidence (command tails)")
    lines.append("")
    for key, res in checks.items():
        lines.append(f"### {key}")
        lines.append("")
        lines.append(f"- cmd: `{res.get('cmd','')}`")
        lines.append(f"- rc: {res.get('returncode')}")
        out = _tail(str(res.get("stdout") or ""))
        err = _tail(str(res.get("stderr") or ""))
        if out:
            lines.append("")
            lines.append("```text")
            lines.append(out)
            lines.append("```")
        if err:
            lines.append("")
            lines.append("```text")
            lines.append(err)
            lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate deterministic governance audit report")
    add_default_args(ap)
    ap.add_argument("--out", default="", help="override output path")
    ap.add_argument("--profile", default="", help="teamos CLI profile for doctor/daemon status")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    ts = utc_now_iso().replace(":", "").replace("-", "")

    out_path = Path(str(args.out or "").strip()) if str(args.out or "").strip() else (repo / "docs" / "audits" / f"DETERMINISTIC_GOV_AUDIT_{ts}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Run checks (no truth-source writes).
    checks: dict[str, dict[str, Any]] = {}
    cli = repo / "teamos"
    if not cli.exists():
        raise PipelineError(f"missing CLI: {cli}")

    prof = str(args.profile or "").strip()
    prof_args = (["--profile", prof] if prof else [])
    checks["doctor"] = _run([str(cli)] + prof_args + ["doctor"], cwd=repo)
    checks["doctor_json"] = _run(
        [
            sys.executable,
            str(repo / "scripts" / "pipelines" / "doctor.py"),
            "--repo-root",
            str(repo),
            "--workspace-root",
            str(ws_root),
            "--json",
        ],
        cwd=repo,
    )
    checks["policy"] = _run([str(cli)] + prof_args + ["policy", "check"], cwd=repo)
    checks["unittest"] = _run([sys.executable, "-m", "unittest", "-q"], cwd=repo)
    checks["req_verify"] = _run(
        [sys.executable, str(repo / "scripts" / "pipelines" / "requirements_raw_first.py"), "--repo-root", str(repo), "--workspace-root", str(ws_root), "verify", "--scope", "teamos"],
        cwd=repo,
    )
    checks["prompt_compile"] = _run([sys.executable, str(repo / "scripts" / "pipelines" / "prompt_compile.py"), "--repo-root", str(repo), "--workspace-root", str(ws_root), "--scope", "teamos", "--dry-run"], cwd=repo)
    checks["db_migrate_plan"] = _run(
        [
            sys.executable,
            str(repo / "scripts" / "pipelines" / "db_migrate.py"),
            "--repo-root",
            str(repo),
            "--workspace-root",
            str(ws_root),
            "--dry-run",
        ],
        cwd=repo,
    )
    checks["approvals_list"] = _run([str(cli)] + prof_args + ["approvals", "list"], cwd=repo)
    checks["cluster_qualify_allowed"] = _run([str(cli)] + prof_args + ["cluster", "qualify"], cwd=repo, env={"TEAMOS_LLM_MODEL_ID": "gpt-5"})
    checks["panel_sync_full_dry_run"] = _run([str(cli)] + prof_args + ["panel", "sync", "--project", "teamos", "--full", "--dry-run"], cwd=repo)

    # Workspace-local project governance smoke tests (use temp workspace; do not mutate real truth sources).
    with tempfile.TemporaryDirectory() as td:
        tmp_ws = Path(td) / "ws"
        tmp_ws.mkdir(parents=True, exist_ok=True)
        pid = "demo"
        proj_repo = tmp_ws / "projects" / pid / "repo"
        checks["project_config"] = _run(
            [
                sys.executable,
                str(repo / "scripts" / "pipelines" / "project_config.py"),
                "--repo-root",
                str(repo),
                "--workspace-root",
                str(tmp_ws),
                "--project",
                pid,
                "init",
            ],
            cwd=repo,
        )
        # Validate after init (separate rc so failures are visible).
        checks["project_config_validate"] = _run(
            [
                sys.executable,
                str(repo / "scripts" / "pipelines" / "project_config.py"),
                "--repo-root",
                str(repo),
                "--workspace-root",
                str(tmp_ws),
                "--project",
                pid,
                "validate",
            ],
            cwd=repo,
        )
        # Inject into project repo AGENTS.md (no leader check in temp; this is a deterministic unit smoke test).
        checks["project_agents_inject"] = _run(
            [
                sys.executable,
                str(repo / "scripts" / "pipelines" / "project_agents_inject.py"),
                "--repo-root",
                str(repo),
                "--workspace-root",
                str(tmp_ws),
                "--project",
                pid,
                "--repo-path",
                str(proj_repo),
                "--manual-version",
                "v1",
                "--no-leader-only",
            ],
            cwd=repo,
        )
        # Idempotency check (second run should be no-op; still rc=0).
        checks["project_agents_inject_idempotent"] = _run(
            [
                sys.executable,
                str(repo / "scripts" / "pipelines" / "project_agents_inject.py"),
                "--repo-root",
                str(repo),
                "--workspace-root",
                str(tmp_ws),
                "--project",
                pid,
                "--repo-path",
                str(proj_repo),
                "--manual-version",
                "v1",
                "--no-leader-only",
            ],
            cwd=repo,
        )

    checks["daemon_status"] = _run([str(cli)] + prof_args + ["daemon", "status"], cwd=repo)
    checks["db_self_improve_runs"] = _run(
        [
            sys.executable,
            "-c",
            (
                "import json, os\n"
                "try:\n"
                "  import psycopg\n"
                "  from psycopg.rows import dict_row\n"
                "except Exception as e:\n"
                "  raise SystemExit('missing psycopg')\n"
                "dsn=os.environ.get('TEAMOS_DB_URL','').strip()\n"
                "if not dsn:\n"
                "  print(json.dumps({'ok': True, 'skipped': True, 'reason': 'TEAMOS_DB_URL not set'}))\n"
                "  raise SystemExit(0)\n"
                "with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:\n"
                "  with conn.cursor() as cur:\n"
                "    cur.execute('SELECT count(*) AS n FROM self_improve_runs')\n"
                "    n = int(cur.fetchone()['n'])\n"
                "    cur.execute('SELECT run_id, applied_count, is_leader, trigger, scope, ts FROM self_improve_runs ORDER BY ts DESC LIMIT 1')\n"
                "    last = cur.fetchone()\n"
                "out={'ok': True, 'skipped': False, 'count': n, 'last': {k: str(v) for k, v in (dict(last) if last else {}).items()}}\n"
                "print(json.dumps(out, ensure_ascii=False))\n"
            ),
        ],
        cwd=repo,
    )

    tasks = _task_rows(repo)

    def _json(key: str) -> dict[str, Any]:
        try:
            obj = json.loads(str(checks.get(key, {}).get("stdout") or "") or "{}")
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    doctorj = _json("doctor_json")
    pg = (doctorj.get("report") or {}).get("postgres_db") if isinstance(doctorj.get("report"), dict) else {}
    pg_status = str((pg or {}).get("status") or "").strip().upper()
    db_enabled = pg_status == "OK"
    if pg_status == "SKIP":
        db_control_status = "WAIVED"
        db_note = str((pg or {}).get("reason") or "postgres backend not configured")
    else:
        db_control_status = "PASS" if db_enabled else "FAIL"
        db_note = "PostgreSQL reachable + migrations applied" if db_enabled else "PostgreSQL check failed (set TEAMOS_DB_URL)"

    approvals = _json("approvals_list")
    approvals_db_enabled = bool(((approvals.get("db") or {}) if isinstance(approvals.get("db"), dict) else {}).get("enabled"))
    approvals_status = "PASS" if approvals_db_enabled else ("WAIVED" if db_control_status == "WAIVED" else "FAIL")

    daemon = _json("daemon_status")
    daemon_running = bool(daemon.get("running")) if isinstance(daemon, dict) else False
    daemon_status = "PASS" if daemon_running else "FAIL"

    si_db = _json("db_self_improve_runs")
    si_skipped = bool(si_db.get("skipped"))
    si_count = int(si_db.get("count") or 0) if not si_skipped else 0
    si_status = "PASS" if (not si_skipped and si_count >= 1) else ("WAIVED" if si_skipped else "FAIL")

    controls: list[dict[str, Any]] = []
    controls.append({"name": "teamos doctor", "status": "PASS" if int(checks["doctor"]["returncode"]) == 0 else "FAIL", "note": "OAuth/gh/control-plane/repo purity/workspace checks"})
    controls.append({"name": "Postgres DB (TEAMOS_DB_URL)", "status": db_control_status, "note": db_note, "evidence": f"status={pg_status or 'UNKNOWN'} migrations={','.join((pg or {}).get('migrations') or []) if isinstance((pg or {}).get('migrations'), list) else ''}"})
    controls.append({"name": "policy check", "status": "PASS" if int(checks["policy"]["returncode"]) == 0 else "FAIL", "note": "secrets filename policy + repo/workspace governance"})
    controls.append({"name": "unit tests", "status": "PASS" if int(checks["unittest"]["returncode"]) == 0 else "FAIL", "note": "python3 -m unittest -q"})
    controls.append({"name": "requirements verify", "status": "PASS" if int(checks["req_verify"]["returncode"]) == 0 else "FAIL", "note": "Raw-First drift/conflict verify (scope=teamos)"})
    controls.append({"name": "prompt compile (dry-run)", "status": "PASS" if int(checks["prompt_compile"]["returncode"]) == 0 else "FAIL", "note": "deterministic prompt compiler (scope=teamos)"})
    controls.append({"name": "db migrations plan (dry-run)", "status": "PASS" if int(checks["db_migrate_plan"]["returncode"]) == 0 else "FAIL", "note": "migration runner present (no DB writes)"})
    controls.append({"name": "approvals list (DB-backed)", "status": approvals_status, "note": "approvals readable from DB (enabled=true required)", "evidence": f"db_enabled={approvals_db_enabled}"})
    controls.append({"name": "central model allowlist qualify", "status": "PASS" if int(checks["cluster_qualify_allowed"]["returncode"]) == 0 else "FAIL", "note": "TEAMOS_LLM_MODEL_ID=gpt-5 is allowed"})
    controls.append({"name": "panel sync (dry-run full)", "status": "PASS" if int(checks["panel_sync_full_dry_run"]["returncode"]) == 0 else "FAIL", "note": "GitHub Projects sync is idempotent; dry-run produces action plan only"})
    controls.append({"name": "project config (smoke)", "status": "PASS" if int(checks["project_config"]["returncode"]) == 0 else "FAIL", "note": "workspace-local project.yaml init (temp workspace)"})
    controls.append({"name": "project config validate (smoke)", "status": "PASS" if int(checks["project_config_validate"]["returncode"]) == 0 else "FAIL", "note": "schema validate (temp workspace)"})
    controls.append({"name": "project AGENTS injection (smoke)", "status": "PASS" if int(checks["project_agents_inject"]["returncode"]) == 0 else "FAIL", "note": "idempotent AGENTS.md injection (temp workspace/repo)"})
    controls.append({"name": "project AGENTS injection idempotent", "status": "PASS" if int(checks["project_agents_inject_idempotent"]["returncode"]) == 0 else "FAIL", "note": "second run should be no-op"})
    controls.append({"name": "self-improve daemon status", "status": daemon_status, "note": "daemon status/state readable; must be running"})
    controls.append({"name": "self-improve runs recorded (DB)", "status": si_status, "note": "self_improve_runs count>=1 when DB enabled", "evidence": f"count={si_count} skipped={si_skipped}"})

    md = _md_report(ts=ts, repo=repo, ws_root=ws_root, checks=checks, tasks=tasks, controls=controls)
    out_path.write_text(md, encoding="utf-8")

    print(json.dumps({"ok": True, "out_path": str(out_path), "ts": ts}, ensure_ascii=False, indent=2))
    any_fail = any(str(c.get("status") or "") == "FAIL" for c in controls)
    return 0 if not any_fail else 2


if __name__ == "__main__":
    raise SystemExit(main())
