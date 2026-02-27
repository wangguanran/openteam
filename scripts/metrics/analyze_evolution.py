#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _scan_task_log_completeness(tasks_dir: Path) -> dict[str, Any]:
    missing: dict[str, list[str]] = {}
    if not tasks_dir.exists():
        return {"tasks_missing_logs": missing}
    for d in sorted(tasks_dir.iterdir()):
        if not d.is_dir():
            continue
        want = [f"{i:02d}_{name}.md" for i, name in [(0, "intake"), (1, "plan"), (2, "todo"), (3, "work"), (4, "test"), (5, "release"), (6, "observe"), (7, "retro")]]
        miss = [f for f in want if not (d / f).exists()]
        if miss:
            missing[d.name] = miss
    return {"tasks_missing_logs": missing}


def analyze(summary: dict[str, Any], *, tasks_dir: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []

    # 1) Telemetry parse errors are always actionable.
    parse_errors = summary.get("parse_errors") or []
    if parse_errors:
        findings.append(
            {
                "kind": "ENGINEERING_GAP",
                "title": "Fix telemetry parse/validation errors in metrics.jsonl",
                "evidence": {"parse_errors": parse_errors[:10]},
                "recommendation": "Run metrics check, locate broken lines, and enforce schema on write.",
            }
        )

    # 2) Missing metrics.jsonl is a compliance gap.
    tasks_path = Path(tasks_dir)
    tasks_with_metrics = set()
    if tasks_path.exists():
        for d in tasks_path.iterdir():
            if d.is_dir() and (d / "metrics.jsonl").exists():
                tasks_with_metrics.add(d.name)
    # We don't know the full task set here; detect missing log bundles instead.
    log_scan = _scan_task_log_completeness(tasks_path)
    if log_scan["tasks_missing_logs"]:
        findings.append(
            {
                "kind": "PROCESS_GAP",
                "title": "Backfill missing task phase logs (00~07) and enforce creation policy",
                "evidence": {"tasks_missing_logs_count": len(log_scan["tasks_missing_logs"]), "sample": dict(list(log_scan["tasks_missing_logs"].items())[:5])},
                "recommendation": "Update new-task/template to always create 00~07; backfill existing tasks with empty phase files.",
            }
        )

    # 3) If there are no events, self-improve has no signal.
    if int(summary.get("total_events") or 0) == 0:
        findings.append(
            {
                "kind": "OBSERVABILITY_GAP",
                "title": "Emit baseline telemetry events (TASK_CREATED, STATUS_CHANGED, SELF_IMPROVE_TRIGGERED)",
                "evidence": {"metrics_files": summary.get("metrics_files"), "total_events": summary.get("total_events")},
                "recommendation": "Add event writers to task creation/state transitions and self-improve scheduler.",
            }
        )

    return {"summary": summary, "log_scan": log_scan, "findings": findings}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Analyze Team OS metrics/logs to propose evolution improvements")
    ap.add_argument("--summary-json", default="", help="Input JSON produced by collect_from_logs.py (optional)")
    ap.add_argument("--tasks-dir", default=str(_repo_root() / ".team-os" / "logs" / "tasks"))
    args = ap.parse_args(argv)

    if args.summary_json:
        summary = _load_json(Path(args.summary_json))
    else:
        # Lazy import to avoid cycles; allow running with no deps.
        from .collect_from_logs import collect  # type: ignore

        summary = collect(Path(args.tasks_dir), strict=False)

    out = analyze(summary, tasks_dir=Path(args.tasks_dir))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
