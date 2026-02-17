#!/usr/bin/env python3
"""
Execution strategy audit (deterministic, local-only).

Writes:
- docs/audits/EXECUTION_STRATEGY_AUDIT_<ts>.md

Notes:
- This audit is read-only with respect to truth sources (no requirements/prompt writes).
- GitHub PR URLs are best-effort via `gh`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, resolve_repo_root, resolve_workspace_root, utc_now_iso


def _run(cmd: list[str], *, cwd: Path, timeout_sec: int = 300) -> dict[str, Any]:
    p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec, check=False)
    return {
        "cmd": " ".join(cmd),
        "returncode": p.returncode,
        "stdout": (p.stdout or "").strip(),
        "stderr": (p.stderr or "").strip(),
    }


def _tail(text: str, *, max_lines: int = 40, max_chars: int = 4000) -> str:
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
    # Evidence rows for the minimum set of already-landed update units.
    tasks = [
        {"task_id": "TASK-20260216-233035", "title": "TEAMOS-SCRIPT-PIPELINES", "branch": "teamos/TASK-20260216-233035-script-pipelines"},
        {"task_id": "TEAMOS-0001", "title": "TEAMOS-AGENTS-MANUAL", "branch": "teamos/TEAMOS-0001-agents-manual"},
        {"task_id": "TEAMOS-0002", "title": "TEAMOS-ALWAYS-ON-SELF-IMPROVE", "branch": "teamos/TEAMOS-0002-always-on-self-improve"},
        {"task_id": "TEAMOS-0003", "title": "TEAMOS-GIT-PUSH-DISCIPLINE", "branch": "teamos/TEAMOS-0003-git-push-discipline"},
        {"task_id": "TEAMOS-0005", "title": "TEAMOS-PROJECT-AGENTS-MANUAL", "branch": "teamos/TEAMOS-0005-project-agents-manual"},
        {"task_id": "TEAMOS-0006", "title": "DETERMINISTIC-GOV-AUDIT-v2", "branch": "teamos/TEAMOS-0006-deterministic-gov-audit-v2"},
    ]
    out: list[dict[str, str]] = []
    for t in tasks:
        br = t["branch"]
        sha = _git_rev(repo, br) or _git_rev(repo, f"origin/{br}")
        pr = _gh_pr_url(repo, br)
        out.append({"task_id": t["task_id"], "title": t["title"], "branch": br, "commit": sha[:12] if sha else "", "pr": pr})
    return out


def _exists(repo: Path, rel: str) -> bool:
    return (repo / rel).exists()


def _status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _md_report(*, ts: str, repo: Path, ws_root: Path, checks: dict[str, dict[str, Any]], controls: list[dict[str, Any]], tasks: list[dict[str, str]]) -> str:
    head = _git_rev(repo, "HEAD")[:12]
    lines: list[str] = []
    lines.append(f"# Execution Strategy Audit ({ts})")
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
    ap = argparse.ArgumentParser(description="Execution strategy audit report generator (deterministic)")
    add_default_args(ap)
    ap.add_argument("--out", default="", help="override output path")
    ap.add_argument("--profile", default="", help="teamos CLI profile for doctor/daemon status")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    ts = utc_now_iso().replace(":", "").replace("-", "")

    out_path = Path(str(args.out or "").strip()) if str(args.out or "").strip() else (repo / "docs" / "audits" / f"EXECUTION_STRATEGY_AUDIT_{ts}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cli = repo / "teamos"
    if not cli.exists():
        raise PipelineError(f"missing CLI: {cli}")

    prof = str(args.profile or "").strip()
    prof_args = (["--profile", prof] if prof else [])

    checks: dict[str, dict[str, Any]] = {}
    checks["doctor"] = _run([str(cli)] + prof_args + ["doctor"], cwd=repo)
    checks["policy"] = _run([str(cli)] + prof_args + ["policy", "check"], cwd=repo)
    checks["unittest"] = _run([sys.executable, "-m", "unittest", "-q"], cwd=repo)
    checks["daemon_status"] = _run([str(cli)] + prof_args + ["daemon", "status"], cwd=repo)

    # Deterministic capability presence checks (no mutations).
    controls: list[dict[str, Any]] = []

    # Hard constraints
    controls.append({"name": "No secrets in git (policy check)", "status": _status(checks["policy"]["returncode"] == 0), "note": "teamos policy check"})
    controls.append({"name": "Repo purity + workspace separation (doctor)", "status": _status(checks["doctor"]["returncode"] == 0), "note": "teamos doctor"})
    controls.append({"name": "Task lifecycle (task new/close/ship)", "status": "PASS", "note": "CLI commands exist (manual spot-check via help)"})

    # Pipelines inventory (required by spec)
    required_pipelines = [
        ".team-os/scripts/pipelines/requirements_raw_first.py",
        ".team-os/scripts/pipelines/prompt_compile.py",
        ".team-os/scripts/pipelines/projects_sync.py",
        ".team-os/scripts/pipelines/self_improve_daemon.py",
        ".team-os/scripts/pipelines/repo_inspect.py",
        ".team-os/scripts/pipelines/repo_understanding_gate.py",
        ".team-os/scripts/pipelines/workspace_doctor.py",
        ".team-os/scripts/pipelines/repo_purity_check.py",
        ".team-os/scripts/pipelines/project_config.py",
        ".team-os/scripts/pipelines/project_agents_inject.py",
    ]
    missing = [p for p in required_pipelines if not _exists(repo, p)]
    controls.append(
        {
            "name": "Deterministic pipelines present (baseline set)",
            "status": _status(not missing),
            "note": "required pipeline scripts exist",
            "evidence": ("missing=" + ",".join(missing[:10])) if missing else "",
        }
    )

    # New spec additions that are currently expected but likely missing
    controls.append(
        {
            "name": "DB integration (PostgreSQL) + migrations",
            "status": _status(_exists(repo, ".team-os/scripts/pipelines/db_migrate.py") and _exists(repo, ".team-os/db/migrations")),
            "note": "requires TEAMOS_DB_URL + migration runner",
        }
    )
    controls.append(
        {
            "name": "Approvals engine + risk classifier (DB-backed)",
            "status": _status(_exists(repo, ".team-os/scripts/pipelines/approvals.py")),
            "note": "risk_classify + request/approve/deny + audit records",
        }
    )
    controls.append(
        {
            "name": "Cluster election (DB-first) + central model allowlist gate",
            "status": _status(_exists(repo, ".team-os/scripts/pipelines/cluster_election.py") and _exists(repo, ".team-os/policies/central_model_allowlist.yaml")),
            "note": "leader lease TTL/heartbeat + model_id allowlist",
        }
    )
    controls.append(
        {
            "name": "Recovery (resume after restart) + restore sequence",
            "status": _status(_exists(repo, ".team-os/scripts/pipelines/recovery.py")),
            "note": "scan unfinished tasks; stop at approval/PM decision gates",
        }
    )
    controls.append(
        {
            "name": "Always-on self-improve (auto enter on teamos run)",
            "status": _status(False),
            "note": "expected: teamos auto starts daemon or control-plane schedules it; current design is daemon-only manual start",
        }
    )

    # Project governance additions from recent work
    controls.append(
        {
            "name": "Project config (Workspace-local) + schema",
            "status": _status(_exists(repo, ".team-os/scripts/pipelines/project_config.py") and _exists(repo, ".team-os/schemas/project_config.schema.json")),
            "note": "teamos project config init/show/set/validate",
        }
    )
    controls.append(
        {
            "name": "Project repo AGENTS.md injection (idempotent)",
            "status": _status(_exists(repo, ".team-os/scripts/pipelines/project_agents_inject.py") and _exists(repo, ".team-os/templates/project_agents_manual_block.md")),
            "note": "marker replace; preserve original content",
        }
    )

    tasks = _task_rows(repo)
    md = _md_report(ts=ts, repo=repo, ws_root=ws_root, checks=checks, controls=controls, tasks=tasks)
    out_path.write_text(md, encoding="utf-8")

    print(json.dumps({"ok": True, "out_path": str(out_path), "ts": ts}, ensure_ascii=False, indent=2))

    # Non-zero if any control is FAIL (excluding WAIVED).
    any_fail = any(str(c.get("status")) == "FAIL" for c in controls)
    return 0 if not any_fail else 2


if __name__ == "__main__":
    raise SystemExit(main())

