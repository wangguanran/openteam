#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import add_default_args
from hub_common import hub_compose_path, hub_root, run_compose, write_json_stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stop Team-OS hub containers")
    add_default_args(ap)
    args = ap.parse_args(argv)

    hub = hub_root()
    compose = hub_compose_path(hub)
    if not compose.exists():
        write_json_stdout({"ok": False, "error": f"missing compose file: {compose}", "hint": "run: teamos hub init"})
        return 2

    out = run_compose(hub=hub, args=["down"], capture=True)
    write_json_stdout({"ok": bool(out.get("ok")), "action": "down", "hub_root": str(hub), "stdout": out.get("stdout", "")[-2000:], "stderr": out.get("stderr", "")[-2000:]})
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
