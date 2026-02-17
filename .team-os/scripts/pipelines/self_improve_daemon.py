#!/usr/bin/env python3
"""
Always-on self-improve daemon (leader-only).

NOTE: This is intentionally minimal in TASK-20260216-233035; full scheduling + proposal generation
is implemented in the dedicated self-improve task (TEAMOS-ALWAYS-ON-SELF-IMPROVE).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, resolve_repo_root, utc_now_iso, write_json


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Self-improve daemon (placeholder)")
    add_default_args(ap)
    ap.add_argument("--once", action="store_true", help="run one iteration then exit (default)")
    ap.add_argument("--loop", action="store_true", help="loop forever (development)")
    ap.add_argument("--interval-sec", type=int, default=3600)
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    state_path = repo / ".team-os" / "state" / "self_improve_state.json"
    # This file is runtime state (should be gitignored); create/update for observability only.
    state: dict[str, Any] = {"schema_version": 1, "last_run": "", "next_run": "", "last_errors": [], "dedupe_keys": []}

    def one() -> None:
        now = utc_now_iso()
        state["last_run"] = now
        state["next_run"] = ""  # filled by real daemon later
        write_json(state_path, state, dry_run=False)

    one()
    if args.loop and not args.once:
        while True:
            time.sleep(max(1, int(args.interval_sec)))
            one()

    print(json.dumps({"ok": True, "mode": "placeholder", "state_path": str(state_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

