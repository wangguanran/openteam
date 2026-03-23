"""Hub subcommand handlers."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from team_os_cli._shared import (
    _approval_gate,
    _find_team_os_repo_root,
    _run_pipeline,
    _workspace_root,
)


def cmd_hub_init(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))]
    argv += ["--pg-port", str(int(getattr(args, "pg_port", 5432) or 5432))]
    argv += ["--redis-port", str(int(getattr(args, "redis_port", 6379) or 6379))]
    _run_pipeline(repo_root, "scripts/pipelines/hub_init.py", argv)


def cmd_hub_up(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    _run_pipeline(repo_root, "scripts/pipelines/hub_up.py", ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))])


def cmd_hub_down(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    _run_pipeline(repo_root, "scripts/pipelines/hub_down.py", ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))])


def cmd_hub_status(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    _run_pipeline(repo_root, "scripts/pipelines/hub_status.py", ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))])


def cmd_hub_logs(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--tail", str(int(getattr(args, "tail", 200) or 200))]
    if str(getattr(args, "service", "") or "").strip():
        argv += ["--service", str(args.service).strip()]
    _run_pipeline(repo_root, "scripts/pipelines/hub_logs.py", argv)


def cmd_hub_migrate(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    _run_pipeline(repo_root, "scripts/pipelines/hub_migrate.py", ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))])


def cmd_hub_expose(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    _approval_gate(
        args,
        repo_root=repo_root,
        action_kind="hub_expose_remote_access",
        summary=f"hub expose bind_ip={args.bind_ip} allow_cidrs={args.allow_cidrs} open_redis={bool(args.open_redis)}",
        payload={
            "bind_ip": str(args.bind_ip),
            "allow_cidrs": str(args.allow_cidrs),
            "open_redis": bool(args.open_redis),
        },
    )
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--bind-ip",
        str(args.bind_ip),
        "--allow-cidrs",
        str(args.allow_cidrs),
    ]
    if bool(args.open_redis):
        argv.append("--open-redis")
    _run_pipeline(repo_root, "scripts/pipelines/hub_expose.py", argv)


def cmd_hub_backup(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))]
    if str(getattr(args, "output", "") or "").strip():
        argv += ["--output", str(args.output).strip()]
    _run_pipeline(repo_root, "scripts/pipelines/hub_backup.py", argv)


def cmd_hub_restore(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    _approval_gate(
        args,
        repo_root=repo_root,
        action_kind="hub_restore",
        summary=f"hub restore file={args.file}",
        payload={"file": str(args.file)},
    )
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--file", str(args.file)]
    _run_pipeline(repo_root, "scripts/pipelines/hub_restore.py", argv)


def cmd_hub_export_config(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--format", str(args.format)]
    _run_pipeline(repo_root, "scripts/pipelines/hub_export_config.py", argv)


def cmd_hub_push_config(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    _approval_gate(
        args,
        repo_root=repo_root,
        action_kind="hub_push_config_with_secrets",
        summary=f"hub push-config host={args.host} user={args.user}",
        payload={"host": str(args.host), "user": str(args.user), "password_stdin": bool(args.password_stdin), "ssh_key": str(args.ssh_key or "")},
    )
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--host",
        str(args.host),
        "--user",
        str(args.user),
        "--remote-env-path",
        str(args.remote_env_path),
    ]
    if str(getattr(args, "ssh_key", "") or "").strip():
        argv += ["--ssh-key", str(args.ssh_key).strip()]
    if str(getattr(args, "hub_host", "") or "").strip():
        argv += ["--hub-host", str(args.hub_host).strip()]
    env = None
    if bool(getattr(args, "password_stdin", False)):
        pw = sys.stdin.read().strip()
        if not pw:
            raise RuntimeError("--password-stdin was provided but stdin was empty")
        argv.append("--password-stdin")
        env = dict(os.environ)
        env["TEAMOS_SSH_PASSWORD"] = pw
    _run_pipeline(repo_root, "scripts/pipelines/hub_push_config.py", argv, env=env)
