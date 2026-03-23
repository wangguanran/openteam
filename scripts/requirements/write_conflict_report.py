#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from team_os_common import utc_now_iso as _utc_now_iso

from _common import requirements_dir, parse_scope


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Write a manual conflict report markdown under conflicts/")
    ap.add_argument("--scope", required=True, help="teamos | project:<id>")
    ap.add_argument("--new-id", required=True, help="new requirement id (e.g. REQ-0007)")
    ap.add_argument("--conflicts-with", required=True, help="comma-separated req ids")
    ap.add_argument("--points", nargs="*", default=[], help="conflict points (optional)")
    args = ap.parse_args(argv)

    scope, pid = parse_scope(args.scope)
    req_dir = requirements_dir(scope, ensure=True)
    ts = _utc_now_iso().replace(":", "").replace("-", "")
    rel = f"conflicts/{ts}-{args.new_id}.md"
    path = req_dir / rel
    conflicts = [x.strip() for x in str(args.conflicts_with or "").split(",") if x.strip()]
    points = [str(x).strip() for x in (args.points or []) if str(x).strip()]

    body = "\n".join(
        [
            f"# Conflict Report ({pid})",
            "",
            f"- created_at: {_utc_now_iso()}",
            f"- new_req: {args.new_id}",
            f"- conflicts_with: {', '.join(conflicts)}",
            "",
            "## Conflict Points",
            "",
            *([f"- {p}" for p in points] or ["- (none)"]),
            "",
            "## Suggested Options (NEED_PM_DECISION)",
            "",
            "### Option A: Accept new requirement, deprecate conflicting ones",
            "- Pros: aligns with latest intent; reduces ambiguity",
            "- Cons: may break existing commitments; needs migration plan",
            "",
            "### Option B: Reject new requirement",
            "- Pros: keeps current baseline stable",
            "- Cons: new request is dropped; may block stakeholder",
            "",
            "### Option C: Narrow scope and keep both",
            "- Pros: can satisfy both parties by scoping",
            "- Cons: higher complexity; requires explicit boundaries",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

