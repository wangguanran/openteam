#!/usr/bin/env python3
import argparse
import json
import sys

from _common import add_template_app_to_syspath, requirements_dir, parse_scope


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Ensure Baseline v1 exists (create-once; never overwrite)")
    ap.add_argument("--scope", required=True, help="openteam | project:<id>")
    ap.add_argument("--seed-text", help="seed text (if omitted, read stdin)")
    ap.add_argument("--raw-input-ts", default="", help="optional raw input timestamp reference")
    ap.add_argument("--channel", default="cli")
    args = ap.parse_args(argv)

    seed = args.seed_text
    if seed is None:
        seed = sys.stdin.read()
    if not str(seed or "").strip():
        raise SystemExit("empty input")

    scope, _pid = parse_scope(args.scope)
    req_dir = requirements_dir(scope, ensure=True)

    add_template_app_to_syspath()
    from app.requirements_store import ensure_baseline_v1  # noqa: E402

    out = ensure_baseline_v1(req_dir, scope=scope, seed_text=seed, raw_input_timestamp=args.raw_input_ts or "", channel=args.channel)
    print(json.dumps(out, ensure_ascii=False, indent=2).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

