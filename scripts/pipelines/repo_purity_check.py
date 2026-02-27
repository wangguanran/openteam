#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, resolve_repo_root


def _import_checker(repo_root: Path):
    # Import the canonical checker (existing governance script).
    p = repo_root / "scripts" / "governance"
    if not (p / "check_repo_purity.py").exists():
        raise PipelineError(f"missing checker: {p / 'check_repo_purity.py'}")
    import sys

    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
    import check_repo_purity  # type: ignore

    return check_repo_purity


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repo purity check (pipeline wrapper; forbids in-repo runtime dynamic paths)")
    add_default_args(ap)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    checker = _import_checker(repo)
    out: dict[str, Any] = checker.check_repo_purity(repo)  # type: ignore[attr-defined]
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        ok = bool(out.get("ok"))
        viol = out.get("violations") or []
        print(f"repo_purity.ok={str(ok).lower()} violations={len(viol)}")
        if not ok:
            for v in viol[:200]:
                print(f"- {v.get('kind')}: {v.get('path')} :: {v.get('detail')}")
    return 0 if bool(out.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
