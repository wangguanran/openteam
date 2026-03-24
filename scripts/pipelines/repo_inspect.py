#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, resolve_repo_root, utc_now_iso, write_text


def _git_sha(repo: Path) -> str:
    p = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def inspect_repo(repo: Path) -> dict[str, Any]:
    entries = [p.name for p in repo.iterdir() if p.name not in (".git", ".DS_Store")]
    non_empty = bool(entries)
    return {
        "repo_root": str(repo),
        "git_sha": _git_sha(repo),
        "non_empty": non_empty,
        "top_level_entries": sorted(entries)[:200],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inspect repo emptiness and write repo_inspect.md")
    add_default_args(ap)
    ap.add_argument("--out", default="docs/product/openteam/repo_inspect.md")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    out = inspect_repo(repo)
    md = "\n".join(
        [
            "# Repo Inspect",
            "",
            f"- generated_at: {utc_now_iso()}",
            f"- repo_root: {repo}",
            f"- git_sha: {out.get('git_sha','')}",
            f"- non_empty: {out.get('non_empty')}",
            "",
            "## Top Level Entries",
            "",
        ]
        + [f"- {x}" for x in (out.get("top_level_entries") or [])]
    ).rstrip() + "\n"
    out_path = (repo / str(args.out)).resolve()
    if not args.dry_run:
        write_text(out_path, md, dry_run=False)
    print(json.dumps({"ok": True, "out_path": str(out_path), "summary": out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
