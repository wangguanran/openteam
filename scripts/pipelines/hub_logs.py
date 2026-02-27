#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import add_default_args
from hub_common import hub_compose_path, hub_root, run_compose


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Show Team-OS hub logs")
    add_default_args(ap)
    ap.add_argument("--service", default="", help="postgres|redis")
    ap.add_argument("--tail", type=int, default=200)
    args = ap.parse_args(argv)

    hub = hub_root()
    compose = hub_compose_path(hub)
    if not compose.exists():
        print(f"ERROR: missing compose file: {compose}\nnext: teamos hub init")
        return 2

    cmd = ["logs", "--tail", str(int(args.tail))]
    if str(args.service or "").strip():
        cmd.append(str(args.service).strip())
    out = run_compose(hub=hub, args=cmd, capture=True)
    if out.get("stdout"):
        print((out.get("stdout") or "").rstrip())
    if out.get("stderr"):
        print((out.get("stderr") or "").rstrip())
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
