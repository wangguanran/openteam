#!/usr/bin/env python3
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from team_os_common import utc_now_iso as _utc_now_iso


def _repo_root() -> Path:
    # <repo>/scripts/metrics/collect_from_logs.py
    return Path(__file__).resolve().parents[2]


@dataclass
class ParseError:
    file: str
    line_no: int
    error: str
    line: str


def _iter_metrics_files(tasks_dir: Path) -> list[Path]:
    if not tasks_dir.exists():
        return []
    out: list[Path] = []
    for d in sorted(tasks_dir.iterdir()):
        if not d.is_dir():
            continue
        p = d / "metrics.jsonl"
        if p.exists():
            out.append(p)
    return out


def _parse_jsonl(path: Path, *, strict: bool) -> tuple[list[dict[str, Any]], list[ParseError]]:
    events: list[dict[str, Any]] = []
    errs: list[ParseError] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            # Minimal required fields check (schema lives in specs/schemas/).
            if not obj.get("ts") or not obj.get("event_type") or not obj.get("actor"):
                raise ValueError("missing required fields: ts/event_type/actor")
            events.append(obj)
        except Exception as e:
            errs.append(ParseError(file=str(path), line_no=i, error=str(e), line=line[:400]))
            if strict:
                break
    return events, errs


def collect(tasks_dir: Path, *, strict: bool = False) -> dict[str, Any]:
    files = _iter_metrics_files(tasks_dir)
    total_events = 0
    by_event_type: dict[str, int] = {}
    by_actor: dict[str, int] = {}
    by_task: dict[str, int] = {}
    parse_errors: list[dict[str, Any]] = []

    for p in files:
        evts, errs = _parse_jsonl(p, strict=strict)
        for e in evts:
            total_events += 1
            et = str(e.get("event_type") or "")
            by_event_type[et] = by_event_type.get(et, 0) + 1
            actor = str(e.get("actor") or "")
            by_actor[actor] = by_actor.get(actor, 0) + 1
            task_id = str(e.get("task_id") or "") or p.parent.name
            by_task[task_id] = by_task.get(task_id, 0) + 1
        for er in errs:
            parse_errors.append({"file": er.file, "line_no": er.line_no, "error": er.error, "line": er.line})
            if strict:
                break

    return {
        "collected_at": _utc_now_iso(),
        "tasks_dir": str(tasks_dir),
        "metrics_files": len(files),
        "total_events": total_events,
        "by_event_type": dict(sorted(by_event_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_actor": dict(sorted(by_actor.items(), key=lambda kv: (-kv[1], kv[0]))),
        "tasks_with_events": len(by_task),
        "by_task": dict(sorted(by_task.items(), key=lambda kv: (-kv[1], kv[0]))),
        "parse_errors": parse_errors[:200],
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Collect Team OS telemetry from task metrics.jsonl")
    ap.add_argument("--tasks-dir", default=str(_repo_root() / ".team-os" / "logs" / "tasks"))
    ap.add_argument("--out", default="", help="Write JSON output to a file (default: stdout)")
    ap.add_argument("--strict", action="store_true", help="Fail fast on first parse/validation error")
    args = ap.parse_args(argv)

    summary = collect(Path(args.tasks_dir), strict=bool(args.strict))
    out_s = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out_s + "\n", encoding="utf-8")
    else:
        print(out_s)
    if args.strict and summary.get("parse_errors"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
