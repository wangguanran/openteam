#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from _common import (
    PipelineError,
    add_default_args,
    is_within,
    read_text,
    render_template,
    resolve_repo_root,
    resolve_workspace_root,
    safe_project_id,
    validate_or_die,
    write_text,
    write_yaml,
)


def _project_config_path(*, repo_root: Path, workspace_root: Path, project_id: str) -> Path:
    # Governance: workspace must be outside the team-os repo.
    if is_within(workspace_root, repo_root):
        raise PipelineError(f"invalid workspace_root={workspace_root} (must be outside repo_root={repo_root})")
    return workspace_root / "projects" / project_id / "state" / "config" / "project.yaml"


def _load_yaml_obj(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    obj = yaml.safe_load(read_text(path)) or {}
    if not isinstance(obj, dict):
        raise PipelineError(f"project config must be a YAML object: {path}")
    return obj


def _set_key(doc: dict[str, Any], key: str, value: Any) -> bool:
    """
    Set dot-path key into doc. Returns changed flag.
    Only supports object nesting (no array indexing).
    """
    k = str(key or "").strip()
    if not k:
        raise PipelineError("--key is required")
    parts = [p for p in k.split(".") if p.strip()]
    if not parts:
        raise PipelineError("invalid --key")

    cur: dict[str, Any] = doc
    for p in parts[:-1]:
        if p not in cur or cur[p] is None:
            cur[p] = {}
        if not isinstance(cur[p], dict):
            raise PipelineError(f"cannot set nested key under non-object: {p}")
        cur = cur[p]

    last = parts[-1]
    old = cur.get(last)
    if old == value:
        return False
    cur[last] = value
    return True


def _render_default_config(repo: Path, *, project_id: str) -> str:
    tpl_path = repo / ".team-os" / "templates" / "project_config.yaml.j2"
    if not tpl_path.exists():
        raise PipelineError(f"missing template: {tpl_path}")
    tpl = read_text(tpl_path)
    return render_template(tpl, {"PROJECT_ID": project_id}).rstrip() + "\n"


def cmd_init(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    pid = safe_project_id(str(args.project_id or ""))
    path = _project_config_path(repo_root=repo, workspace_root=ws, project_id=pid)

    changed = False
    if not path.exists():
        txt = _render_default_config(repo, project_id=pid)
        write_text(path, txt, dry_run=bool(args.dry_run))
        changed = not bool(args.dry_run)

    out = {"ok": True, "project_id": pid, "path": str(path), "changed": changed, "dry_run": bool(args.dry_run)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    pid = safe_project_id(str(args.project_id or ""))
    path = _project_config_path(repo_root=repo, workspace_root=ws, project_id=pid)
    if not path.exists():
        raise PipelineError(f"missing project config: {path} (run: teamos project config init --project {pid})")
    print(read_text(path).rstrip() + "\n")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    pid = safe_project_id(str(args.project_id or ""))
    path = _project_config_path(repo_root=repo, workspace_root=ws, project_id=pid)
    if not path.exists():
        raise PipelineError(f"missing project config: {path} (run: teamos project config init --project {pid})")

    doc = _load_yaml_obj(path)
    validate_or_die(doc, repo / ".team-os" / "schemas" / "project_config.schema.json", label="project_config")
    if str(doc.get("project_id") or "").strip() != pid:
        raise PipelineError(f"project_id mismatch in config: want={pid} got={doc.get('project_id')!r}")

    out = {"ok": True, "project_id": pid, "path": str(path)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    pid = safe_project_id(str(args.project_id or ""))
    path = _project_config_path(repo_root=repo, workspace_root=ws, project_id=pid)
    if not path.exists():
        raise PipelineError(f"missing project config: {path} (run: teamos project config init --project {pid})")

    doc = _load_yaml_obj(path)
    # Parse value using YAML scalar/object parsing for deterministic typing.
    try:
        value = yaml.safe_load(str(args.value))
    except Exception:
        value = str(args.value)

    changed = _set_key(doc, str(args.key or ""), value)
    validate_or_die(doc, repo / ".team-os" / "schemas" / "project_config.schema.json", label="project_config")
    if str(doc.get("project_id") or "").strip() != pid:
        raise PipelineError(f"project_id mismatch in config: want={pid} got={doc.get('project_id')!r}")

    if changed and (not bool(args.dry_run)):
        write_yaml(path, doc, dry_run=False)
    out = {"ok": True, "project_id": pid, "path": str(path), "changed": changed, "dry_run": bool(args.dry_run)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Project config (Workspace-local; schema-validated)")
    add_default_args(ap)
    ap.add_argument("--project-id", "--project", dest="project_id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    sp = ap.add_subparsers(dest="cmd", required=True)

    sp.add_parser("init", help="Create default project.yaml if missing (idempotent)").set_defaults(fn=cmd_init)
    sp.add_parser("show", help="Show project.yaml").set_defaults(fn=cmd_show)

    st = sp.add_parser("set", help="Set a config key (dot-path) and validate schema")
    st.add_argument("--key", required=True)
    st.add_argument("--value", required=True)
    st.set_defaults(fn=cmd_set)

    sp.add_parser("validate", help="Validate project.yaml against schema").set_defaults(fn=cmd_validate)

    args = ap.parse_args(argv)
    try:
        return int(args.fn(args))
    except PipelineError as e:
        print(f"ERROR: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

