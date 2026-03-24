#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from _common import add_default_args, resolve_repo_root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Enter Team-OS project REPL (raw v3 capture)")
    add_default_args(ap)
    ap.add_argument("--project", default="")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    cli = repo / "openteam"
    if not cli.exists():
        print(json.dumps({"ok": False, "error": f"missing cli: {cli}"}, ensure_ascii=False, indent=2))
        return 2

    cmd = [str(cli)]
    if str(args.workspace_root or "").strip():
        cmd += ["--workspace-root", str(args.workspace_root).strip()]
    if str(args.project or "").strip():
        # Explicitly enter chat mode when project id is provided.
        cmd += ["chat", "--project", str(args.project).strip()]

    p = subprocess.run(cmd, cwd=str(repo), stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, check=False)
    return int(p.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
