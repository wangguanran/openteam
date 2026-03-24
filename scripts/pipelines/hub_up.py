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
    write_json_stdout,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Start Team-OS hub containers")
    add_default_args(ap)
    args = ap.parse_args(argv)

    hub = hub_root()
    try:
        load_hub_env_required(hub)
        validate_hub_compose_required(hub)
        enforce_hub_env_config_security(hub)
    except PipelineError as e:
        write_json_stdout({"ok": False, "error": str(e), "hint": "run: openteam hub init"})
        return 2

    out = run_compose(hub=hub, args=["up", "-d"], capture=True)
    write_json_stdout({"ok": bool(out.get("ok")), "action": "up", "hub_root": str(hub), "stdout": out.get("stdout", "")[-2000:], "stderr": out.get("stderr", "")[-2000:]})
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
