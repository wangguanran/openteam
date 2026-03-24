#!/usr/bin/env python3
import argparse
import json
import sys

import yaml

from _common import add_template_app_to_syspath, requirements_dir, parse_scope


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="New-Input Conflict Check (offline heuristics)")
    ap.add_argument("--scope", required=True, help="openteam | project:<id>")
    ap.add_argument("--text", help="new raw requirement text (if omitted, read stdin)")
    args = ap.parse_args(argv)

    text = args.text
    if text is None:
        text = sys.stdin.read()
    if not str(text or "").strip():
        raise SystemExit("empty input")

    scope, _pid = parse_scope(args.scope)
    req_dir = requirements_dir(scope, ensure=False)

    add_template_app_to_syspath()
    from app.req_conflict import detect_conflicts, detect_duplicate, infer_workstreams  # noqa: E402

    y = req_dir / "requirements.yaml"
    existing = []
    if y.exists():
        try:
            data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
            existing = list(data.get("requirements") or [])
        except Exception:
            existing = []

    dup = detect_duplicate(existing, text)
    findings = detect_conflicts(existing, text)
    conflicts_with = sorted({f.req_id for f in findings})
    classification = "COMPATIBLE"
    if dup:
        classification = "DUPLICATE"
    elif conflicts_with:
        classification = "CONFLICT"

    out = {
        "classification": classification,
        "duplicate_of": dup,
        "conflicts_with": conflicts_with,
        "workstreams": infer_workstreams(text),
        "findings": [{"req_id": f.req_id, "topic": f.topic, "existing": f.existing_stance, "new": f.new_stance} for f in findings],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

