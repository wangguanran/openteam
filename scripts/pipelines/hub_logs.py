#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import PipelineError, add_default_args
from hub_common import (
    enforce_hub_env_config_security,
    hub_root,
    load_hub_env_required,
    run_compose,
    validate_hub_compose_required,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Show Team-OS hub logs")
    add_default_args(ap)
    ap.add_argument("--service", default="", help="postgres|redis")
    ap.add_argument("--tail", type=int, default=200)
    args = ap.parse_args(argv)

    hub = hub_root()
    try:
        load_hub_env_required(hub)
        validate_hub_compose_required(hub)
        enforce_hub_env_config_security(hub)
    except PipelineError as e:
        print(f"ERROR: {e}\nnext: openteam hub init")
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
