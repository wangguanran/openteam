"""Project subcommand handlers."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from team_os_cli._shared import (
    _base_url,
    _ensure_project_scaffold,
    _find_team_os_repo_root,
    _fmt_table,
    _inject_project_agents_manual,
    _project_repo_dir,
    _require_project_id,
    _run_pipeline,
    _workspace_root,
    eprint,
)
from team_os_cli.http import _http_json


def cmd_project_list(args: argparse.Namespace) -> None:
    root = _workspace_root(args)
    projects_dir = root / "projects"
    if not projects_dir.exists():
        print(f"workspace_missing: {root}")
        print("next: teamos workspace init")
        raise SystemExit(2)
    rows: list[list[str]] = []
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        pid = d.name
        repo = d / "repo"
        state = d / "state"
        req = state / "requirements" / "requirements.yaml"
        tasks = state / "ledger" / "tasks"
        rows.append(
            [
                pid,
                "Y" if repo.exists() else "",
                "Y" if state.exists() else "",
                "Y" if req.exists() else "",
                str(len(list(tasks.glob("*.yaml")))) if tasks.exists() else "0",
            ]
        )
    print(_fmt_table(["project_id", "repo", "state", "requirements", "tasks"], rows))


def _project_repl(args: argparse.Namespace, *, project_id: str) -> int:
    base, _prof = _base_url(args)
    print(f"project_repl: project_id={project_id} scope=project:{project_id}")
    print("\u8f93\u5165\u4f1a\u843d\u76d8\u4e3a Raw\uff0c\u4e0d\u8981\u8f93\u5165\u5bc6\u7801/\u5bc6\u94a5\u3002")
    print("Enter requirement text. Commands: /exit /help /status")
    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            break
        if not line:
            break
        text = line.rstrip("\n")
        if not text.strip():
            continue
        cmd = text.strip()
        if cmd in ("/exit", "/quit"):
            break
        if cmd == "/help":
            print("commands: /exit /help /status ; any other text is captured as RAW requirement")
            continue
        if cmd == "/status":
            st = _http_json("GET", base + "/v1/status")
            instance_id = str(st.get("instance_id") or "").strip()
            leader_base = ""
            if isinstance(st.get("leader"), dict):
                leader_base = str((st.get("leader") or {}).get("leader_base_url") or "").strip()
            print(f"status.instance_id={instance_id}")
            if leader_base:
                print(f"status.leader_base_url={leader_base}")
            continue
        out = _http_json(
            "POST",
            base + "/v1/requirements/add",
            {"scope": f"project:{project_id}", "text": text, "source": "cli", "workstream_id": "general"},
            timeout_sec=120,
        )
        print(str(out.get("summary") or "").rstrip())
    return 0


def cmd_project_config_init(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the team-os repo.")

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        pid,
    ]
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    argv.append("init")
    _run_pipeline(repo_root, "scripts/pipelines/project_config.py", argv)

    # Hook: ensure project repo AGENTS.md contains the manual block.
    _inject_project_agents_manual(args, project_id=pid, reason="project_config_init")


def cmd_project_config_show(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the team-os repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        pid,
        "show",
    ]
    _run_pipeline(repo_root, "scripts/pipelines/project_config.py", argv)


def cmd_project_config_set(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the team-os repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        pid,
    ]
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    argv += ["set", "--key", str(args.key), "--value", str(args.value)]
    _run_pipeline(repo_root, "scripts/pipelines/project_config.py", argv)


def cmd_project_config_validate(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the team-os repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        pid,
        "validate",
    ]
    _run_pipeline(repo_root, "scripts/pipelines/project_config.py", argv)

    # Hook: if validate runs, ensure AGENTS manual exists (idempotent).
    _inject_project_agents_manual(args, project_id=pid, reason="project_config_validate")


def cmd_project_agents_inject(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_path = str(getattr(args, "repo_path", "") or "").strip() or str(_project_repo_dir(_workspace_root(args), pid))
    _inject_project_agents_manual(args, project_id=pid, repo_path=repo_path, reason="explicit")
