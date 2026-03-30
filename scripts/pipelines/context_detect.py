#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import add_default_args, resolve_repo_root, resolve_workspace_root


def _detect_project_from_cwd(workspace_root: Path, cwd: Path) -> str:
    base = (workspace_root / "projects").resolve()
    cur = cwd.resolve()
    try:
        rel = cur.relative_to(base)
    except Exception:
        return ""
    parts = rel.parts
    if len(parts) >= 2 and parts[1] == "repo":
        return str(parts[0] or "").strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detect Team-OS context from cwd")
    add_default_args(ap)
    ap.add_argument("--cwd", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    cwd = Path(str(args.cwd or "").strip()).expanduser().resolve() if str(args.cwd or "").strip() else Path.cwd().resolve()
    pid = _detect_project_from_cwd(ws, cwd)
    out = {
        "ok": True,
        "repo_root": str(repo),
        "workspace_root": str(ws),
        "cwd": str(cwd),
        "in_project_repo": bool(pid),
        "project_id": pid,
        "scope": (f"project:{pid}" if pid else "openteam"),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"scope={out['scope']} project_id={pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
