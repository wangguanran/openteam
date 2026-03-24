#!/usr/bin/env python3
import argparse
import json
import sys

from _common import add_template_app_to_syspath, requirements_dir, parse_scope


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Requirements Protocol v2 (Raw-First): capture -> drift/conflict -> expand")
    ap.add_argument("--scope", required=True, help="openteam | project:<id>")
    ap.add_argument("--channel", default="cli", help="cli|api|chat|import")
    ap.add_argument("--user", default="user")
    ap.add_argument("--priority", default="P2")
    ap.add_argument("--rationale", default="")
    ap.add_argument("--source", default="cli")
    ap.add_argument("--text", help="requirement text (if omitted, read stdin)")
    args = ap.parse_args(argv)

    text = args.text
    if text is None:
        text = sys.stdin.read()
    if not str(text or "").strip():
        raise SystemExit("empty input")

    scope, pid = parse_scope(args.scope)
    req_dir = requirements_dir(scope, ensure=True)

    add_template_app_to_syspath()
    from app.requirements_store import add_requirement_raw_first  # noqa: E402

    out = add_requirement_raw_first(
        project_id=pid,
        req_dir=req_dir,
        requirement_text=text,
        priority=args.priority,
        rationale=args.rationale,
        constraints=None,
        acceptance=None,
        source=args.source,
        channel=args.channel,
        user=args.user,
    )
    print(
        json.dumps(
            {
                "classification": out.classification,
                "req_id": out.req_id,
                "duplicate_of": out.duplicate_of,
                "conflicts_with": out.conflicts_with,
                "conflict_report_path": out.conflict_report_path,
                "drift_report_path": out.drift_report_path,
                "raw_input_timestamp": out.raw_input_timestamp,
                "baseline_path": out.baseline_path,
                "actions_taken": out.actions_taken,
                "pending_decisions": out.pending_decisions,
            },
            ensure_ascii=False,
            indent=2,
        ).rstrip()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

