"""Requirements subcommand handlers."""
from __future__ import annotations

import argparse
import json
import urllib.parse
from pathlib import Path
from typing import Any

from team_os_cli._shared import (
    _base_url,
    _default_scope,
    _ensure_project_scaffold,
    _fmt_table,
    _inject_project_agents_manual,
    _norm,
    _require_project_id,
    _workspace_root,
)
from team_os_cli.http import _http_json


def cmd_req_add(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    # Project-scope guardrails: ensure Workspace scaffold exists and AGENTS manual is injected.
    pid_for_scope = ""
    if str(scope or "").strip().startswith("project:"):
        pid_for_scope = _require_project_id(str(scope).split(":", 1)[1])
        _ensure_project_scaffold(_workspace_root(args), pid_for_scope)
    payload = {
        "scope": scope,
        "workstream_id": args.workstream,
        "text": args.text,
        "priority": args.priority,
        "rationale": args.rationale or "",
        "constraints": args.constraints or None,
        "acceptance": args.acceptance or None,
        "source": args.source or "cli",
    }
    out = _http_json("POST", base + "/v1/requirements/add", payload, timeout_sec=120)
    print(out.get("summary", "").rstrip())
    if out.get("pending_decisions"):
        print("\nPENDING_DECISIONS:")
        for d in out["pending_decisions"]:
            print(json.dumps(d, ensure_ascii=False))
    if pid_for_scope:
        _inject_project_agents_manual(args, project_id=pid_for_scope, reason="requirements_add")


def cmd_req_list(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    out = _http_json("GET", base + "/v1/requirements/show?scope=" + urllib.parse.quote(scope))
    reqs = out.get("requirements") or []
    rows = []
    for r in reqs:
        st = str(r.get("status", ""))
        if args.show_conflicts:
            rows.append(
                [
                    str(r.get("req_id", "")),
                    st,
                    str(r.get("priority", "")),
                    ",".join(r.get("conflicts_with") or []),
                    (",".join(r.get("decision_log_refs") or [])[:80]),
                    str(r.get("title", ""))[:60],
                ]
            )
        else:
            rows.append([str(r.get("req_id", "")), st, str(r.get("priority", "")), str(r.get("title", ""))[:60]])
    if rows:
        if args.show_conflicts:
            print(_fmt_table(["req_id", "status", "prio", "conflicts_with", "refs", "title"], rows))
        else:
            print(_fmt_table(["req_id", "status", "prio", "title"], rows))
    else:
        print("(none)")


def cmd_req_conflicts(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    out = _http_json("GET", base + "/v1/requirements/show?scope=" + urllib.parse.quote(scope))
    reqs = out.get("requirements") or []
    rows = []
    for r in reqs:
        st = str(r.get("status", "")).upper()
        if st in ("CONFLICT", "NEED_PM_DECISION"):
            rows.append(
                [
                    str(r.get("req_id", "")),
                    st,
                    ",".join(r.get("conflicts_with") or []),
                    (",".join(r.get("decision_log_refs") or [])[:80]),
                    str(r.get("title", ""))[:50],
                ]
            )
    if rows:
        print(_fmt_table(["req_id", "status", "conflicts_with", "refs", "title"], rows))
    else:
        print("(none)")


def cmd_req_import(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    pid_for_scope = ""
    if str(scope or "").strip().startswith("project:"):
        pid_for_scope = _require_project_id(str(scope).split(":", 1)[1])
        _ensure_project_scaffold(_workspace_root(args), pid_for_scope)
    p = Path(args.file).expanduser()
    if not p.exists():
        raise RuntimeError(f"file not found: {p}")
    content = p.read_text(encoding="utf-8")
    payload = {"scope": scope, "filename": p.name, "content_text": content, "workstream_id": args.workstream, "source": "import"}
    out = _http_json("POST", base + "/v1/requirements/import", payload, timeout_sec=120)
    print(out.get("summary", "").rstrip())
    if out.get("pending_decisions"):
        print("\nPENDING_DECISIONS:")
        for d in out["pending_decisions"]:
            print(json.dumps(d, ensure_ascii=False))
    if pid_for_scope:
        _inject_project_agents_manual(args, project_id=pid_for_scope, reason="requirements_import")


def cmd_req_verify(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    out = _http_json("POST", base + "/v1/requirements/verify", {"scope": scope}, timeout_sec=60)
    ok = bool(out.get("ok"))
    print(f"ok={ok} scope={scope}")
    drift = out.get("drift") or {}
    if not drift.get("ok"):
        print("drift: FAIL")
        for p in drift.get("points") or []:
            print(f"- {p}")
    conflicts = out.get("conflicts") or []
    if conflicts:
        print(f"conflicts: {len(conflicts)}")
        for c in conflicts[:50]:
            print(json.dumps(c, ensure_ascii=False))


def cmd_req_rebuild(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    pid_for_scope = ""
    if str(scope or "").strip().startswith("project:"):
        pid_for_scope = _require_project_id(str(scope).split(":", 1)[1])
        _ensure_project_scaffold(_workspace_root(args), pid_for_scope)
    out = _http_json("POST", base + "/v1/requirements/rebuild", {"scope": scope}, timeout_sec=60)
    print(json.dumps(out, ensure_ascii=False, indent=2).rstrip())
    if pid_for_scope:
        _inject_project_agents_manual(args, project_id=pid_for_scope, reason="requirements_rebuild")


def cmd_req_baseline_show(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    url = base + "/v1/requirements/baseline/show?scope=" + urllib.parse.quote(scope) + "&max_chars=" + urllib.parse.quote(str(args.max_chars))
    out = _http_json("GET", url, timeout_sec=60)
    print(f"scope={scope}")
    items = out.get("baselines") or []
    if not items:
        print("(none)")
        return
    for it in items[:50]:
        name = _norm(it.get("name"))
        path = _norm(it.get("path"))
        print(f"\n== {name} ==")
        if path:
            print(f"path={path}")
        prev = _norm(it.get("text_preview"))
        if prev:
            print(prev.rstrip())


def cmd_req_baseline_set_v2(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    payload = {"scope": scope, "text": args.text, "reason": args.reason}
    out = _http_json("POST", base + "/v1/requirements/baseline/set-v2", payload, timeout_sec=120)
    print(json.dumps(out, ensure_ascii=False, indent=2).rstrip())
