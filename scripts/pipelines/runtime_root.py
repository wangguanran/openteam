#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from _common import add_default_args, resolve_repo_root, runtime_root


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def _ensure_runtime_dirs(rt: Path) -> dict[str, str]:
    dirs = {
        "runtime_root": rt,
        "state": rt / "state",
        "workspace": rt / "workspace",
        "hub": rt / "hub",
        "tmp": rt / "tmp",
        "cache": rt / "cache",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
        _chmod_best_effort(d, 0o700)
    # subdirs commonly used by bootstrap/doctor
    for d in [
        rt / "state" / "audit",
        rt / "state" / "logs",
        rt / "state" / "runs",
        rt / "state" / "teamos",
        rt / "state" / "kb" / "sources",
        rt / "workspace" / "projects",
        rt / "workspace" / "shared" / "cache",
        rt / "workspace" / "shared" / "tmp",
        rt / "workspace" / "config",
    ]:
        d.mkdir(parents=True, exist_ok=True)
        _chmod_best_effort(d, 0o700)
    return {k: str(v) for k, v in dirs.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve Team-OS runtime root and ensure required directories")
    add_default_args(ap)
    ap.add_argument("--ensure", action="store_true", help="create required runtime directories")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    override = str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip()
    rt = runtime_root(override=override if override else str(repo.parent / "team-os-runtime"))

    out = {
        "ok": True,
        "repo_root": str(repo),
        "runtime_root": str(rt),
        "runtime_root_source": "env" if override else "default_repo_parent",
    }
    if args.ensure:
        out["dirs"] = _ensure_runtime_dirs(rt)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"repo_root={out['repo_root']}")
        print(f"runtime_root={out['runtime_root']} source={out['runtime_root_source']}")
        if args.ensure:
            for k, v in (out.get("dirs") or {}).items():
                print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
