#!/usr/bin/env python3
import argparse
import json
import sys

from _common import add_template_app_to_syspath, requirements_dir, parse_scope


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Raw-First: capture requirement raw input (append-only)")
    ap.add_argument("--scope", required=True, help="openteam | project:<id>")
    ap.add_argument("--channel", default="cli", help="cli|api|chat|import|migration|baseline")
    ap.add_argument("--user", default="")
    ap.add_argument("--text", help="raw text (if omitted, read stdin)")
    args = ap.parse_args(argv)

    text = args.text
    if text is None:
        text = sys.stdin.read()
    if not str(text or "").strip():
        raise SystemExit("empty input")

    scope, _pid = parse_scope(args.scope)
    req_dir = requirements_dir(scope, ensure=True)

    add_template_app_to_syspath()
    from app.requirements_store import capture_raw_input  # noqa: E402

    out = capture_raw_input(req_dir, scope=scope, text=text, channel=args.channel, user=args.user)
    print(json.dumps(out, ensure_ascii=False, indent=2).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
