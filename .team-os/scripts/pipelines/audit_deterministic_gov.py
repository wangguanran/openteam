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
    ]
    out: list[dict[str, str]] = []
    for t in tasks:
        br = t["branch"]
        sha = _git_rev(repo, br) or _git_rev(repo, f"origin/{br}")
        pr = _gh_pr_url(repo, br)
        out.append({"task_id": t["task_id"], "title": t["title"], "branch": br, "commit": sha[:12] if sha else "", "pr": pr})
    return out


def _md_report(*, ts: str, repo: Path, ws_root: Path, checks: dict[str, dict[str, Any]], tasks: list[dict[str, str]]) -> str:
    def status(rc: int) -> str:
        return "PASS" if rc == 0 else "FAIL"

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
    # Core controls
    core = [
        ("teamos doctor", checks["doctor"]["returncode"], "OAuth/gh/control-plane/repo purity/workspace checks"),
        ("policy check", checks["policy"]["returncode"], "secrets filename policy + repo/workspace governance"),
        ("unit tests", checks["unittest"]["returncode"], "python3 -m unittest -q"),
        ("requirements verify", checks["req_verify"]["returncode"], "Raw-First drift/conflict verify (scope=teamos)"),
        ("prompt compile (dry-run)", checks["prompt_compile"]["returncode"], "deterministic prompt compiler (scope=teamos)"),
        ("self-improve daemon status", checks["daemon_status"]["returncode"], "daemon status/state readable (leader-only writes)"),
    ]
    for name, rc, note in core:
        lines.append(f"- {name}: {status(int(rc))}  ({note})")
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
    checks["policy"] = _run([str(cli)] + prof_args + ["policy", "check"], cwd=repo)
    checks["unittest"] = _run([sys.executable, "-m", "unittest", "-q"], cwd=repo)
    checks["req_verify"] = _run(
        [sys.executable, str(repo / ".team-os" / "scripts" / "pipelines" / "requirements_raw_first.py"), "--repo-root", str(repo), "--workspace-root", str(ws_root), "verify", "--scope", "teamos"],
        cwd=repo,
    )
    checks["prompt_compile"] = _run([sys.executable, str(repo / ".team-os" / "scripts" / "pipelines" / "prompt_compile.py"), "--repo-root", str(repo), "--workspace-root", str(ws_root), "--scope", "teamos", "--dry-run"], cwd=repo)
    checks["daemon_status"] = _run([str(cli)] + prof_args + ["daemon", "status"], cwd=repo)

    tasks = _task_rows(repo)
    md = _md_report(ts=ts, repo=repo, ws_root=ws_root, checks=checks, tasks=tasks)
    out_path.write_text(md, encoding="utf-8")

    print(json.dumps({"ok": True, "out_path": str(out_path), "ts": ts}, ensure_ascii=False, indent=2))
    # Return non-zero if any core check failed.
    core_rc = [int(checks[k]["returncode"]) for k in ("doctor", "policy", "unittest", "req_verify", "prompt_compile", "daemon_status")]
    return 0 if all(rc == 0 for rc in core_rc) else 2


if __name__ == "__main__":
    raise SystemExit(main())

