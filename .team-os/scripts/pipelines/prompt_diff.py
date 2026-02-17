#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
from pathlib import Path

from _common import PipelineError, add_default_args, read_text, render_template, resolve_repo_root, resolve_workspace_root, sha256_file, sha256_text
from prompt_compile import _operating_rules, _parse_scope, _prompt_base_dir, _requirements_dir, _load_requirements_summary  # keep in sync with prompt_compile.py


def _render_master_prompt(*, repo: Path, ws: Path, scope: str, project_id: str) -> str:
    req_dir = _requirements_dir(repo=repo, ws_root=ws, scope=scope, project_id=project_id)
    tpl_path = repo / ".team-os" / "templates" / "prompt_master.md.j2"
    if not tpl_path.exists():
        raise PipelineError(f"missing template: {tpl_path}")
    tpl = read_text(tpl_path)

    baseline_path = req_dir / "baseline" / "original_description_v1.md"
    baseline_txt = read_text(baseline_path) if baseline_path.exists() else "(missing baseline v1)"
    baseline_excerpt = baseline_txt.strip()

    req_summary = _load_requirements_summary(req_dir, project_id=project_id)

    baseline_sha = sha256_file(baseline_path) if baseline_path.exists() else ""
    req_sha = sha256_file(req_dir / "requirements.yaml") if (req_dir / "requirements.yaml").exists() else ""
    tpl_sha = sha256_file(tpl_path)
    build_id = sha256_text("\n".join([baseline_sha, req_sha, tpl_sha]))

    manifest_ref = "prompt_manifest.json" if scope != "teamos" else "prompt-library/teamos/prompt_manifest.json"

    body = (
        render_template(
            tpl,
            {
                "PROJECT_ID": project_id,
                "BUILD_ID": build_id,
                "BASELINE_SHA256": baseline_sha,
                "REQUIREMENTS_SHA256": req_sha,
                "MANIFEST_PATH": manifest_ref,
                "BASELINE_EXCERPT": baseline_excerpt,
                "REQUIREMENTS_SUMMARY": req_summary.rstrip(),
                "OPERATING_RULES": _operating_rules().rstrip(),
            },
        ).rstrip()
        + "\n"
    )
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Show unified diff for MASTER_PROMPT.md vs deterministic build output")
    add_default_args(ap)
    ap.add_argument("--scope", required=True, help="teamos | project:<id>")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    scope, pid = _parse_scope(str(args.scope))

    prompt_dir = _prompt_base_dir(repo=repo, ws_root=ws, scope=scope, project_id=pid)
    master_path = prompt_dir / "MASTER_PROMPT.md"
    old = read_text(master_path) if master_path.exists() else ""
    new = _render_master_prompt(repo=repo, ws=ws, scope=scope, project_id=pid)

    if (old or "").replace("\r\n", "\n") == (new or "").replace("\r\n", "\n"):
        print("prompt_diff: clean (no changes)")
        return 0

    a = (old or "").replace("\r\n", "\n").splitlines(keepends=True)
    b = (new or "").replace("\r\n", "\n").splitlines(keepends=True)
    diff = difflib.unified_diff(a, b, fromfile=str(master_path), tofile="(deterministic build)", lineterm="\n")
    for line in diff:
        # difflib already includes trailing newlines when lineterm="\n"
        print(line, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

