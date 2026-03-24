#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openteam_common import utc_now_iso as _utc_now_iso

from _common import requirements_dir, parse_scope


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Append an entry to CHANGELOG.md (requirements scope)")
    ap.add_argument("--scope", required=True, help="openteam | project:<id>")
    ap.add_argument("--message", required=True)
    args = ap.parse_args(argv)

    _scope, pid = parse_scope(args.scope)
    req_dir = requirements_dir(args.scope, ensure=True)
    path = req_dir / "CHANGELOG.md"
    if not path.exists():
        path.write_text(f"# Requirements Changelog ({pid})\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {_utc_now_iso()} {args.message.strip()}\n")
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

