#!/usr/bin/env python3
from __future__ import annotations

import atexit
import argparse
import json
import os
from pathlib import Path
from typing import Any

import yaml

import locks

from _common import (
    PipelineError,
    add_default_args,
    read_text,
    render_template,
    resolve_repo_root,
    resolve_workspace_root,
    sha256_file,
    sha256_text,
    ts_compact_utc,
    validate_or_die,
    write_json,
    write_text,
)


def _prompt_base_dir(*, repo: Path, ws_root: Path, scope: str, project_id: str) -> Path:
    if scope == "teamos":
        return repo / "specs" / "prompts" / "teamos"
    # project scope prompts must live in Workspace
    from _common import is_within

    if is_within(ws_root, repo):
        raise PipelineError(f"invalid workspace_root={ws_root} (must be outside repo={repo})")
    return ws_root / "projects" / project_id / "state" / "prompts"


def _requirements_dir(*, repo: Path, ws_root: Path, scope: str, project_id: str) -> Path:
    if scope == "teamos":
        return repo / "docs" / "product" / "teamos" / "requirements"
    from _common import is_within

    if is_within(ws_root, repo):
        raise PipelineError(f"invalid workspace_root={ws_root} (must be outside repo={repo})")
    return ws_root / "projects" / project_id / "state" / "requirements"


def _parse_scope(scope: str) -> tuple[str, str]:
    s = str(scope or "").strip()
    if not s:
        raise PipelineError("missing --scope teamos|project:<id>")
    if s == "teamos":
        return ("teamos", "teamos")
    if s.startswith("project:"):
        pid = s.split(":", 1)[1].strip()
        if not pid:
            raise PipelineError("invalid scope: project:<id> missing id")
        from _common import safe_project_id

        return (s, safe_project_id(pid))
    return _parse_scope(f"project:{s}")


def _load_requirements_summary(req_dir: Path, *, project_id: str) -> str:
    y = req_dir / "requirements.yaml"
    if not y.exists():
        return "- (requirements.yaml missing)\n"
    data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
    reqs = list(data.get("requirements") or [])
    by_status: dict[str, list[dict[str, Any]]] = {}
    for r in reqs:
        st = str(r.get("status") or "ACTIVE").upper()
        by_status.setdefault(st, []).append(r)
    out: list[str] = []
    out.append(f"### Requirements ({project_id})")
    for st in ["ACTIVE", "NEED_PM_DECISION", "CONFLICT", "DEPRECATED"]:
        items = sorted(by_status.get(st, []), key=lambda x: str(x.get("req_id") or ""))
        out.append("")
        out.append(f"#### {st}")
        if not items:
            out.append("- (none)")
        else:
            for r in items[:500]:
                rid = str(r.get("req_id") or "").strip()
                pr = str(r.get("priority") or "").strip()
                title = str(r.get("title") or "").strip()
                ws = ",".join((r.get("workstreams") or [])[:10])
                out.append(f"- {rid} [{pr}] {title} (ws={ws})".rstrip())
    return "\n".join(out).rstrip() + "\n"


def _operating_rules() -> str:
    # Keep short, deterministic, and aligned with AGENTS.md hard rules.
    lines = [
        "- No secrets in git. Use env vars only; only commit `.env.example`.",
        "- Repo vs Workspace: project truth sources MUST be outside the team-os repo.",
        "- Deterministic pipelines only for truth-source generation (requirements/prompt/task ledger).",
        "- High risk actions require explicit approval (data deletion/overwrite, public ports, prod deploy, force push).",
        "- Leader-only writes: only the elected Brain writes truth sources; assistants are read-only unless leased.",
    ]
    return "\n".join(lines) + "\n"


def _relpath(p: Path, *, repo: Path, ws_root: Path) -> str:
    try:
        return str(p.resolve().relative_to(repo.resolve()))
    except Exception:
        pass
    try:
        return str(p.resolve().relative_to(ws_root.resolve()))
    except Exception:
        pass
    return str(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compile MASTER_PROMPT.md deterministically from baseline+requirements+policies")
    add_default_args(ap)
    ap.add_argument("--scope", required=True, help="teamos | project:<id>")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    scope, pid = _parse_scope(str(args.scope))

    req_dir = _requirements_dir(repo=repo, ws_root=ws, scope=scope, project_id=pid)
    prompt_dir = _prompt_base_dir(repo=repo, ws_root=ws, scope=scope, project_id=pid)

    # Concurrency: repo lock (teamos only) + scope lock.
    repo_lock = None
    scope_lock = None
    if scope == "teamos":
        repo_lock = locks.acquire_repo_lock(repo_root=repo, task_id=str(os.getenv("TEAMOS_TASK_ID") or ""))
    scope_lock = locks.acquire_scope_lock(
        scope,
        repo_root=repo,
        workspace_root=ws,
        req_dir=req_dir,
        task_id=str(os.getenv("TEAMOS_TASK_ID") or ""),
    )

    def _cleanup_locks() -> None:
        locks.release_lock(scope_lock)
        locks.release_lock(repo_lock)

    atexit.register(_cleanup_locks)

    (prompt_dir / "history").mkdir(parents=True, exist_ok=True)

    tpl_path = repo / "templates" / "content" / "prompt_master.md.j2"
    if not tpl_path.exists():
        raise PipelineError(f"missing template: {tpl_path}")
    tpl = read_text(tpl_path)

    baseline_path = req_dir / "baseline" / "original_description_v1.md"
    baseline_txt = read_text(baseline_path) if baseline_path.exists() else "(missing baseline v1)"
    baseline_excerpt = baseline_txt.strip()

    req_summary = _load_requirements_summary(req_dir, project_id=pid)

    manifest_path = prompt_dir / "prompt_manifest.json"
    master_path = prompt_dir / "MASTER_PROMPT.md"
    changelog_path = prompt_dir / "PROMPT_CHANGELOG.md"

    baseline_sha = sha256_file(baseline_path) if baseline_path.exists() else ""
    req_sha = sha256_file(req_dir / "requirements.yaml") if (req_dir / "requirements.yaml").exists() else ""
    tpl_sha = sha256_file(tpl_path)
    build_id = sha256_text("\n".join([baseline_sha, req_sha, tpl_sha]))

    # Keep prompt content deterministic across runs and machines:
    # - no timestamps
    # - no absolute paths
    manifest_ref = "prompt_manifest.json" if scope != "teamos" else "specs/prompts/teamos/prompt_manifest.json"

    body = render_template(
        tpl,
        {
            "PROJECT_ID": pid,
            "BUILD_ID": build_id,
            "BASELINE_SHA256": baseline_sha,
            "REQUIREMENTS_SHA256": req_sha,
            "MANIFEST_PATH": manifest_ref,
            "BASELINE_EXCERPT": baseline_excerpt,
            "REQUIREMENTS_SUMMARY": req_summary.rstrip(),
            "OPERATING_RULES": _operating_rules().rstrip(),
        },
    ).rstrip() + "\n"

    new_sha = sha256_text(body)
    old_sha = sha256_file(master_path) if master_path.exists() else ""
    changed = (not old_sha) or (new_sha != old_sha)

    history_path: str | None = None
    if (not changed) and manifest_path.exists() and changelog_path.exists():
        # Idempotent fast-path: nothing to write.
        out = {"ok": True, "scope": scope, "project_id": pid, "changed": False, "master_prompt_path": str(master_path), "manifest_path": str(manifest_path)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if changed and not args.dry_run:
        ts = ts_compact_utc()
        hp = prompt_dir / "history" / f"{ts}.md"
        history_path = str(hp)
        if master_path.exists() and (not hp.exists()):
            hp.write_text(read_text(master_path), encoding="utf-8")

        write_text(master_path, body, dry_run=False)

        if not changelog_path.exists():
            changelog_path.write_text(f"# Prompt Changelog ({pid})\n\n", encoding="utf-8")
        with changelog_path.open("a", encoding="utf-8") as f:
            f.write(f"- {ts} build_id={build_id} sha256={new_sha} baseline_sha256={baseline_sha or 'missing'} requirements_sha256={req_sha or 'missing'}\n")

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "project_id": pid,
        "generated_at": ts_compact_utc(),
        "build_id": build_id,
        "inputs": {
            "baseline_v1_path": _relpath(baseline_path, repo=repo, ws_root=ws),
            "baseline_v1_sha256": baseline_sha,
            "requirements_yaml_path": _relpath(req_dir / "requirements.yaml", repo=repo, ws_root=ws),
            "requirements_yaml_sha256": req_sha,
            "template_path": _relpath(tpl_path, repo=repo, ws_root=ws),
            "template_sha256": tpl_sha,
        },
        "outputs": {
            "master_prompt_path": _relpath(master_path, repo=repo, ws_root=ws),
            "history_path": _relpath(Path(history_path), repo=repo, ws_root=ws) if history_path else None,
            "changelog_path": _relpath(changelog_path, repo=repo, ws_root=ws),
        },
        "content_sha256": new_sha,
    }
    validate_or_die(manifest, repo / "specs" / "schemas" / "prompt_manifest.schema.json", label="prompt_manifest")
    if not args.dry_run:
        write_json(manifest_path, manifest, dry_run=False)

    out = {"ok": True, "scope": scope, "project_id": pid, "changed": changed, "master_prompt_path": str(master_path), "manifest_path": str(manifest_path)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
