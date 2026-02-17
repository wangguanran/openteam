#!/usr/bin/env python3
"""
Req v3 + locks E2E audit (deterministic, local-first).

Writes:
- docs/audits/REQV3_LOCKS_AUDIT_<ts>.md

This audit intentionally exercises the deterministic pipelines to prove:
- Raw input v3 is user-only (no assessments/system/self-improve pollution)
- Every raw input gets a feasibility report + sidecar assessment index
- NOT_FEASIBLE/NEEDS_INFO gates expansion into NEED_PM_DECISION (no "executable" expansion)
- Self-improve writes proposals + updates Expanded without writing raw inputs
- Concurrency locks regression tests pass (unittest includes evals/test_concurrency_locks.py)
- Approvals can be recorded to Postgres when TEAMOS_DB_URL is set
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from _common import PipelineError, add_default_args, resolve_repo_root, resolve_workspace_root, utc_now_iso, validate_or_die


def _run(cmd: list[str], *, cwd: Path, timeout_sec: int = 300) -> dict[str, Any]:
    p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec, check=False)
    return {"cmd": " ".join(cmd), "returncode": p.returncode, "stdout": (p.stdout or "").strip(), "stderr": (p.stderr or "").strip()}


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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    if not path.exists():
        return ""
    h.update(path.read_bytes())
    return h.hexdigest()


def _read_jsonl_last(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    last: dict[str, Any] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict):
            last = obj
    return last


def _read_jsonl_last_by_key(path: Path, *, key: str, value: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    found: dict[str, Any] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if str(obj.get(key) or "").strip() == str(value or "").strip():
            found = obj
    return found


def _redact_dsn(dsn: str) -> str:
    s = str(dsn or "").strip()
    if not s:
        return ""
    # postgresql://user:pass@host/db -> postgresql://user:***@host/db
    return re.sub(r"(postgres(?:ql)?://[^:/@]+):([^@]+)@", r"\1:***@", s)


def _status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _md_report(
    *,
    ts: str,
    repo: Path,
    ws_root: Path,
    checks: list[dict[str, Any]],
    cmd_tails: dict[str, dict[str, Any]],
) -> str:
    head = _git_rev(repo, "HEAD")[:12]
    lines: list[str] = []
    lines.append(f"# REQV3 + Locks Audit ({ts})")
    lines.append("")
    lines.append("## Context")
    lines.append("")
    lines.append(f"- repo: {repo}")
    lines.append(f"- workspace_root: {ws_root}")
    lines.append(f"- git_sha: {head}")
    lines.append("")
    lines.append("## Checks (PASS/FAIL/SKIP)")
    lines.append("")
    for c in checks:
        lines.append(f"- {c.get('name')}: {c.get('status')}  ({c.get('note','')})".rstrip())
        ev = str(c.get("evidence") or "").strip()
        if ev:
            lines.append(f"  - evidence: {ev}")
    lines.append("")
    lines.append("## Evidence (command tails)")
    lines.append("")
    for key in sorted(cmd_tails.keys()):
        res = cmd_tails[key]
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


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="REQv3 + Locks E2E audit generator (deterministic)")
    add_default_args(ap)
    ap.add_argument("--out", default="", help="override output path")
    ap.add_argument("--project-id", default="audit-e2e", help="workspace project id for req v3 checks (default: audit-e2e)")
    ap.add_argument("--skip-self-improve", action="store_true")
    ap.add_argument("--skip-db", action="store_true", help="skip db migrate + approvals write checks")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    ts = utc_now_iso().replace(":", "").replace("-", "")

    out_path = Path(str(args.out or "").strip()) if str(args.out or "").strip() else (repo / "docs" / "audits" / f"REQV3_LOCKS_AUDIT_{ts}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []
    cmd_tails: dict[str, dict[str, Any]] = {}

    # --- A) Req v3: FEASIBLE ---
    pid = str(args.project_id or "").strip()
    if not pid:
        raise PipelineError("--project-id is required")

    req_dir = ws_root / "projects" / pid / "state" / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)

    req_script = repo / ".team-os" / "scripts" / "pipelines" / "requirements_raw_first.py"
    if not req_script.exists():
        raise PipelineError(f"missing pipeline: {req_script}")

    feasible_text = f"[E2E:{ts}] Add a feasible requirement for v3 verification."
    r1 = _run(
        [
            sys.executable,
            str(req_script),
            "--repo-root",
            str(repo),
            "--workspace-root",
            str(ws_root),
            "add",
            "--scope",
            f"project:{pid}",
            "--text",
            feasible_text,
            "--workstream",
            "qa",
            "--priority",
            "P2",
            "--source",
            "cli",
            "--user",
            "e2e",
        ],
        cwd=repo,
        timeout_sec=120,
    )
    cmd_tails["req_add_feasible"] = r1
    ok_r1 = r1["returncode"] == 0

    raw_path = req_dir / "raw_inputs.jsonl"
    raw_last = _read_jsonl_last(raw_path)
    assess_path = req_dir / "raw_assessments.jsonl"
    raw_id1 = str(raw_last.get("raw_id") or "").strip()
    assess1 = _read_jsonl_last_by_key(assess_path, key="raw_id", value=raw_id1) if raw_id1 else {}
    report_rel1 = str(assess1.get("report_path") or "").strip()
    report_path1 = (req_dir / report_rel1).resolve() if report_rel1 else Path()
    report_text1 = report_path1.read_text(encoding="utf-8", errors="replace") if report_rel1 and report_path1.exists() else ""

    raw_keys_ok = set(raw_last.keys()) == {"raw_id", "timestamp", "scope", "user", "channel", "text", "text_sha256"} if raw_last else False
    assess_ok = bool(assess1) and str(assess1.get("outcome") or "").strip().upper() in ("FEASIBLE", "PARTIALLY_FEASIBLE")
    feas_file_ok = bool(report_rel1) and report_path1.exists() and ("## Outcome" in report_text1)

    checks.append(
        {
            "name": "REQv3 FEASIBLE: raw-only + assessment + feasibility report",
            "status": _status(ok_r1 and raw_keys_ok and assess_ok and feas_file_ok),
            "note": f"project:{pid}",
            "evidence": f"raw_id={raw_id1} outcome={str(assess1.get('outcome') or '')} report={report_rel1}",
        }
    )

    # Validate schema for last raw + assessment when possible.
    try:
        validate_or_die(raw_last, repo / ".team-os" / "schemas" / "requirement_raw_input.schema.json", label="requirement_raw_input")
        validate_or_die(assess1, repo / ".team-os" / "schemas" / "requirement_raw_assessment.schema.json", label="requirement_raw_assessment")
        checks.append({"name": "REQv3 schema validation (raw/assessment)", "status": "PASS", "note": "jsonschema", "evidence": ""})
    except Exception as e:
        checks.append({"name": "REQv3 schema validation (raw/assessment)", "status": "FAIL", "note": "jsonschema", "evidence": str(e)[:200]})

    # --- B) Req v3: NOT_FEASIBLE -> NEED_PM_DECISION ---
    not_feasible_text = f"[E2E:{ts}] 将项目 requirements 写入 team-os repo 并提交"
    r2 = _run(
        [
            sys.executable,
            str(req_script),
            "--repo-root",
            str(repo),
            "--workspace-root",
            str(ws_root),
            "add",
            "--scope",
            f"project:{pid}",
            "--text",
            not_feasible_text,
            "--workstream",
            "qa",
            "--priority",
            "P1",
            "--source",
            "cli",
            "--user",
            "e2e",
        ],
        cwd=repo,
        timeout_sec=120,
    )
    cmd_tails["req_add_not_feasible"] = r2
    ok_r2 = r2["returncode"] == 0

    raw_last2 = _read_jsonl_last(raw_path)
    raw_id2 = str(raw_last2.get("raw_id") or "").strip()
    assess2 = _read_jsonl_last_by_key(assess_path, key="raw_id", value=raw_id2) if raw_id2 else {}
    outcome2 = str(assess2.get("outcome") or "").strip().upper()
    report_rel2 = str(assess2.get("report_path") or "").strip()
    report_path2 = (req_dir / report_rel2).resolve() if report_rel2 else Path()

    # Verify Expanded contains a NEED_PM_DECISION item referencing this raw_id (no "executable" requirement created).
    expanded_path = req_dir / "requirements.yaml"
    need_pm_ok = False
    if expanded_path.exists():
        try:
            doc = yaml.safe_load(expanded_path.read_text(encoding="utf-8")) or {}
            reqs = list(doc.get("requirements") or []) if isinstance(doc, dict) else []
            for it in reqs:
                if not isinstance(it, dict):
                    continue
                if str(it.get("status") or "").strip().upper() != "NEED_PM_DECISION":
                    continue
                refs = it.get("raw_input_refs") or []
                if raw_id2 and raw_id2 in refs:
                    need_pm_ok = True
                    break
        except Exception:
            need_pm_ok = False

    checks.append(
        {
            "name": "REQv3 NOT_FEASIBLE gates expansion (NEED_PM_DECISION)",
            "status": _status(ok_r2 and outcome2 == "NOT_FEASIBLE" and report_rel2 and report_path2.exists() and need_pm_ok),
            "note": f"project:{pid}",
            "evidence": f"raw_id={raw_id2} outcome={outcome2} report={report_rel2} need_pm_item={need_pm_ok}",
        }
    )

    # --- C) Self-Improve separation (teamos scope) ---
    teamos_raw = repo / "docs" / "teamos" / "requirements" / "raw_inputs.jsonl"
    raw_before = _sha256_file(teamos_raw)
    si_ok = True
    si_note = ""
    si_ev = ""
    if bool(getattr(args, "skip_self_improve", False)):
        checks.append({"name": "Self-Improve separation (no raw writes)", "status": "SKIP", "note": "skipped by flag", "evidence": ""})
        si_ok = True
    else:
        si_script = repo / ".team-os" / "scripts" / "pipelines" / "self_improve_daemon.py"
        r3 = _run(
            [
                sys.executable,
                str(si_script),
                "--repo-root",
                str(repo),
                "--workspace-root",
                str(ws_root),
                "run-once",
                "--scope",
                "teamos",
                "--force",
            ],
            cwd=repo,
            timeout_sec=300,
        )
        cmd_tails["self_improve_run_once"] = r3
        raw_after = _sha256_file(teamos_raw)
        si_ok = (r3["returncode"] == 0) and (raw_before == raw_after)
        si_note = "raw_inputs.jsonl sha256 unchanged"
        # Best-effort parse output json for proposal_path.
        proposal_path = ""
        try:
            obj = json.loads(r3.get("stdout") or "{}")
            if isinstance(obj, dict):
                proposal_path = str(obj.get("proposal_path") or "").strip()
        except Exception:
            proposal_path = ""
        si_ev = f"raw_before={raw_before[:12]} raw_after={raw_after[:12]} proposal_path={proposal_path or '(unknown)'}"
        checks.append({"name": "Self-Improve separation (no raw writes)", "status": _status(si_ok), "note": si_note, "evidence": si_ev})

    # --- D) Concurrency locks regression (unittest) ---
    r4 = _run([sys.executable, "-m", "unittest", "-q"], cwd=repo, timeout_sec=600)
    cmd_tails["unittest"] = r4
    checks.append({"name": "Concurrency locks regression (unittest)", "status": _status(r4["returncode"] == 0), "note": "includes evals/test_concurrency_locks.py", "evidence": ""})

    # --- E) DB approvals record (Postgres) ---
    dsn = str(os.environ.get("TEAMOS_DB_URL") or "").strip()
    if bool(getattr(args, "skip_db", False)):
        checks.append({"name": "Approvals DB write", "status": "SKIP", "note": "skipped by flag", "evidence": ""})
    elif not dsn:
        checks.append({"name": "Approvals DB write", "status": "FAIL", "note": "TEAMOS_DB_URL not set", "evidence": ""})
    else:
        # Migrate DB schema.
        db_cli = repo / "teamos"
        r5 = _run([str(db_cli), "db", "migrate"], cwd=repo, timeout_sec=300)
        cmd_tails["db_migrate"] = r5
        mig_ok = r5["returncode"] == 0

        apv_script = repo / ".team-os" / "scripts" / "pipelines" / "approvals.py"
        r6 = _run(
            [
                sys.executable,
                str(apv_script),
                "--repo-root",
                str(repo),
                "--workspace-root",
                str(ws_root),
                "request",
                "--task-id",
                "TEAMOS-0019",
                "--action-kind",
                "repo_create",
                "--summary",
                f"[E2E:{ts}] approvals db write check",
                "--role",
                "single",
                "--yes",
            ],
            cwd=repo,
            timeout_sec=60,
        )
        cmd_tails["approvals_request"] = r6
        req_ok = r6["returncode"] == 0
        approval_id = ""
        status = ""
        try:
            obj = json.loads(r6.get("stdout") or "{}")
            if isinstance(obj, dict):
                approval_id = str(obj.get("approval_id") or "").strip()
                status = str(obj.get("status") or "").strip()
        except Exception:
            pass

        r7 = _run([str(db_cli), "approvals", "list", "--limit", "5"], cwd=repo, timeout_sec=60)
        cmd_tails["approvals_list"] = r7
        list_ok = r7["returncode"] == 0 and bool((r7.get("stdout") or "").strip())

        checks.append(
            {
                "name": "Approvals DB write",
                "status": _status(mig_ok and req_ok and list_ok),
                "note": f"TEAMOS_DB_URL={_redact_dsn(dsn)}",
                "evidence": f"approval_id={approval_id or '(unknown)'} status={status or '(unknown)'}",
            }
        )

    report = _md_report(ts=ts, repo=repo, ws_root=ws_root, checks=checks, cmd_tails=cmd_tails)
    out_path.write_text(report, encoding="utf-8")

    print(json.dumps({"ok": True, "out": str(out_path)}, ensure_ascii=False, indent=2))
    failed = [c for c in checks if str(c.get("status")) == "FAIL"]
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
