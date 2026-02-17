#!/usr/bin/env python3
"""
Idempotently inject/update a Team-OS manual block into a project repo's AGENTS.md.

Governance:
- Uses fixed markers for replacement:
  - <!-- TEAMOS_MANUAL_START -->
  - <!-- TEAMOS_MANUAL_END -->
- Preserves all project content outside the marked block.
- Leader-only writes by default (non-leader runs are plan-only).
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from _common import (
    PipelineError,
    add_default_args,
    is_within,
    read_text,
    render_template,
    resolve_repo_root,
    resolve_workspace_root,
    safe_project_id,
    write_text,
)


START = "<!-- TEAMOS_MANUAL_START -->"
END = "<!-- TEAMOS_MANUAL_END -->"


def _http_json(url: str, *, timeout_sec: int = 5) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            obj = json.loads(body) if body else {}
            return obj if isinstance(obj, dict) else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise PipelineError(f"HTTP {e.code} {e.reason}: {body[:300]}") from e
    except Exception as e:
        raise PipelineError(f"HTTP request failed: {e}") from e


def _load_base_url(*, profile: str = "") -> str:
    cfg = Path.home() / ".teamos" / "config.toml"
    if not cfg.exists():
        return "http://127.0.0.1:8787"
    try:
        try:
            import tomli  # type: ignore

            doc = tomli.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            import tomllib

            doc = tomllib.loads(cfg.read_bytes())
    except Exception:
        return "http://127.0.0.1:8787"

    cur = str(profile or doc.get("current_profile") or "").strip()
    profiles = doc.get("profiles") or {}
    if not cur:
        cur = "local" if "local" in profiles else (sorted(list(profiles.keys()))[0] if profiles else "")
    p = (profiles or {}).get(cur) or {}
    base = str(p.get("base_url") or "").strip().rstrip("/")
    return base or "http://127.0.0.1:8787"


def _leader_status(*, base_url: str) -> dict[str, Any]:
    st = _http_json(base_url + "/v1/status", timeout_sec=5)
    cs = _http_json(base_url + "/v1/cluster/status", timeout_sec=5)
    me = str(st.get("instance_id") or "").strip()
    leader = (cs.get("leader") or {}) if isinstance(cs.get("leader"), dict) else {}
    leader_id = str(leader.get("leader_instance_id") or "").strip()
    is_leader = bool(me and leader_id and me == leader_id)
    return {
        "ok": True,
        "base_url": base_url,
        "instance_id": me,
        "leader_instance_id": leader_id,
        "is_leader": is_leader,
    }


def _render_block(repo: Path, *, project_id: str, manual_version: str) -> str:
    tpl_path = repo / ".team-os" / "templates" / "project_agents_manual_block.md"
    if not tpl_path.exists():
        raise PipelineError(f"missing template: {tpl_path}")
    tpl = read_text(tpl_path)
    md = render_template(
        tpl,
        {
            "PROJECT_ID": project_id,
            "MANUAL_VERSION": str(manual_version or "").strip() or "v1",
        },
    )
    # Ensure markers exist in the rendered block.
    if START not in md or END not in md:
        raise PipelineError("template must include TEAMOS_MANUAL_START/END markers")
    return md.rstrip() + "\n"


def _compute_new_text(*, old: str, block: str) -> tuple[str, str]:
    """
    Returns (mode, new_text).
    mode: create|replace|append|noop
    """
    old_norm = (old or "").replace("\r\n", "\n")
    if not old_norm.strip():
        base = "# AGENTS.md\n\n(项目自定义指导可写在本文件其他部分；Team-OS 注入区块见下方。)\n\n"
        new_text = base + block
        return ("create", new_text)

    if START in old_norm or END in old_norm:
        if (START in old_norm) and (END in old_norm):
            # Replace inclusive marker block.
            pat = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
            new_text = pat.sub(block.strip("\n"), old_norm, count=1)
            new_text = new_text.rstrip() + "\n"
            return ("replace", new_text)
        raise PipelineError("invalid AGENTS.md markers: must contain both TEAMOS_MANUAL_START and TEAMOS_MANUAL_END")

    # Append at end.
    sep = "" if old_norm.endswith("\n") else "\n"
    new_text = old_norm + sep + "\n" + block
    new_text = new_text.rstrip() + "\n"
    return ("append", new_text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inject/Update Team-OS manual block in project repo AGENTS.md (idempotent)")
    add_default_args(ap)
    ap.add_argument("--project-id", "--project", dest="project_id", required=True)
    ap.add_argument("--project-repo-path", "--repo-path", dest="project_repo_path", default="", help="default: <workspace>/projects/<id>/repo")
    ap.add_argument("--manual-version", default="v1")
    ap.add_argument("--profile", default="", help="teamos profile name (for leader check)")
    ap.add_argument("--base-url", default="", help="override control plane base url (for leader check)")
    ap.add_argument("--no-leader-only", action="store_false", dest="leader_only", help="allow writes even if not leader (not recommended)")
    ap.add_argument("--leader-only", action="store_true", default=True, help="leader-only writes (default)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)
    pid = safe_project_id(str(args.project_id or ""))

    repo_path = Path(str(args.project_repo_path or "").strip()).expanduser().resolve() if str(args.project_repo_path or "").strip() else (ws / "projects" / pid / "repo")

    # Governance: do not let project repos live inside the team-os git repo.
    if is_within(repo_path, repo):
        raise PipelineError(f"project_repo_path is inside team-os repo (refusing): {repo_path}")
    # Strong preference: project repos live in workspace.
    if not is_within(repo_path, ws):
        raise PipelineError(f"project_repo_path must be within workspace_root (refusing): repo={repo_path} workspace={ws}")

    # Ensure repo dir exists (Workspace-local, safe).
    if not repo_path.exists() and (not bool(args.dry_run)):
        repo_path.mkdir(parents=True, exist_ok=True)
    if not repo_path.exists():
        raise PipelineError(f"project_repo_path does not exist: {repo_path}")

    agents_path = repo_path / "AGENTS.md"
    old = read_text(agents_path) if agents_path.exists() else ""
    block = _render_block(repo, project_id=pid, manual_version=str(args.manual_version or "v1"))
    mode, new_text = _compute_new_text(old=old, block=block)

    changed = (new_text != (old or "").replace("\r\n", "\n"))

    leader: dict[str, Any] = {"ok": False, "is_leader": False, "reason": "unknown"}
    can_write = True
    if bool(getattr(args, "leader_only", True)):
        can_write = False
        base = str(args.base_url or "").strip().rstrip("/") or _load_base_url(profile=str(args.profile or ""))
        try:
            leader = _leader_status(base_url=base)
            can_write = bool(leader.get("is_leader"))
            if not can_write:
                leader["reason"] = "not_leader"
        except Exception as e:
            leader = {"ok": False, "is_leader": False, "reason": str(e)[:200], "base_url": base}
            can_write = False

    wrote = False
    if changed and can_write and (not bool(args.dry_run)):
        write_text(agents_path, new_text, dry_run=False)
        wrote = True

    out = {
        "ok": True,
        "project_id": pid,
        "project_repo_path": str(repo_path),
        "agents_path": str(agents_path),
        "manual_version": str(args.manual_version or "v1"),
        "mode": mode,
        "changed": changed,
        "wrote": wrote,
        "dry_run": bool(args.dry_run),
        "leader_only": bool(getattr(args, "leader_only", True)),
        "leader": leader,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

