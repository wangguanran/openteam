"""Cluster and node subcommand handlers."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

from team_os_cli._shared import (
    _approval_gate,
    _base_url,
    _extract_stage_from_json_output,
    _find_team_os_repo_root,
    _fmt_table,
    _record_installer_run,
    _run_pipeline,
    _workspace_root,
)
from team_os_cli.http import _http_json


def cmd_cluster_status(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    out = _http_json("GET", base + "/v1/cluster/status", timeout_sec=10)
    leader = out.get("leader") or {}
    nodes = out.get("nodes") or []
    pending = out.get("pending_decisions") or []
    llm = out.get("llm_profile") or {}
    qual = out.get("leader_qualification") or {}
    print(f"profile={prof['name']} base_url={base}")
    print(f"leader.instance_id={leader.get('leader_instance_id','')}")
    print(f"leader.backend={leader.get('backend','')}")
    if llm:
        if llm.get("provider"):
            print(f"llm.provider={llm.get('provider')}")
        if llm.get("model_id"):
            print(f"llm.model_id={llm.get('model_id')}")
        if llm.get("auth_mode"):
            print(f"llm.auth_mode={llm.get('auth_mode')}")
    if qual:
        print(f"leader_qualification.qualified={qual.get('qualified')}")
        if qual.get("reason"):
            print(f"leader_qualification.reason={qual.get('reason')}")
    if leader.get("leader_base_url"):
        print(f"leader.base_url={leader.get('leader_base_url')}")
    if pending:
        print(f"PENDING_DECISIONS={len(pending)}")
    print(f"nodes={len(nodes)}")
    if nodes:
        rows = []
        for n in nodes[:50]:
            rows.append(
                [
                    str(n.get("instance_id", ""))[:8],
                    str(n.get("role_preference", "")),
                    str(n.get("heartbeat_at", "")),
                    ",".join(n.get("capabilities") or [])[:30],
                    ",".join(n.get("tags") or [])[:30],
                ]
            )
        print(_fmt_table(["node", "role_pref", "heartbeat", "capabilities", "tags"], rows))


def cmd_cluster_qualify(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the repo.")
    _run_pipeline(
        repo_root,
        "scripts/pipelines/cluster_election.py",
        ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "qualify"],
    )


def cmd_node_join_script(args: argparse.Namespace) -> None:
    # Print a join command to run on the new server (no secrets included).
    base, prof = _base_url(args)
    brain_url = args.brain_base_url or base
    cluster_repo = args.cluster_repo
    if not cluster_repo:
        raise RuntimeError("missing --cluster-repo owner/name")
    caps = args.capabilities or ""
    tags = args.tags or ""
    role = args.role or "auto"
    print(
        f'bash scripts/cluster/join_node.sh --cluster-repo "{cluster_repo}" --brain-base-url "{brain_url}" --role "{role}" --capabilities "{caps}" --tags "{tags}"'
    )


def cmd_node_add(args: argparse.Namespace) -> None:
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Run from within the repo or set TEAM_OS_REPO_PATH.")
    script = repo_root / "scripts" / "cluster" / "bootstrap_remote_node.sh"
    if not script.exists():
        raise RuntimeError(f"missing script: {script}")
    base, prof = _base_url(args)
    argv = [
        "bash",
        str(script),
        "--host",
        args.host,
        "--user",
        args.user,
        "--cluster-repo",
        args.cluster_repo,
        "--brain-base-url",
        args.brain_base_url or base,
        "--role",
        args.role or "auto",
        "--capabilities",
        args.capabilities or "",
        "--tags",
        args.tags or "",
    ]
    if args.ssh_key:
        argv += ["--ssh-key", args.ssh_key]
    ws_root = _workspace_root(args)
    child_env: Optional[dict[str, str]] = None
    stdin_password = ""
    if bool(getattr(args, "password_stdin", False)):
        stdin_password = sys.stdin.read().rstrip("\r\n")
        if not stdin_password:
            raise RuntimeError("--password-stdin was provided but stdin was empty")
        argv += ["--password-stdin"]
        child_env = dict(os.environ)
        child_env["TEAMOS_SSH_PASSWORD"] = stdin_password
    if args.execute:
        _approval_gate(
            args,
            repo_root=repo_root,
            action_kind="node_add_execute",
            summary=f"node add --execute host={args.host} user={args.user} cluster_repo={args.cluster_repo}",
            payload={
                "host": args.host,
                "user": args.user,
                "cluster_repo": args.cluster_repo,
                "brain_base_url": args.brain_base_url or base,
                "role": args.role or "auto",
                "capabilities": args.capabilities or "",
                "tags": args.tags or "",
                "ssh_key": args.ssh_key or "",
                "password_stdin": bool(getattr(args, "password_stdin", False)),
                "push_hub_config": bool(getattr(args, "push_hub_config", False)),
                "hub_host": str(getattr(args, "hub_host", "") or ""),
                "remote_env_path": str(getattr(args, "remote_env_path", "") or "~/.teamos/node.env"),
            },
        )
        argv += ["--execute"]
    p = subprocess.run(
        argv,
        check=False,
        env=child_env or None,
        input=(stdin_password + "\n") if bool(getattr(args, "password_stdin", False)) else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sys.stdout.write(p.stdout or "")
    sys.stderr.write(p.stderr or "")
    _record_installer_run(
        repo_root=repo_root,
        workspace_root=ws_root,
        component="node_add.bootstrap",
        stage="bootstrap_remote_node",
        target_host=str(args.host),
        ok=(p.returncode == 0),
        stdout_text=str(p.stdout or ""),
        stderr_text=str(p.stderr or ""),
    )
    if p.returncode != 0:
        raise SystemExit(p.returncode)
    # Optional: push Brain hub config to the new node.
    if bool(getattr(args, "execute", False)) and bool(getattr(args, "push_hub_config", False)):
        argv2 = [
            "--repo-root",
            str(repo_root),
            "--workspace-root",
            str(ws_root),
            "--host",
            str(args.host),
            "--user",
            str(args.user),
            "--remote-env-path",
            str(getattr(args, "remote_env_path", "") or "~/.teamos/node.env"),
        ]
        if str(getattr(args, "hub_host", "") or "").strip():
            argv2 += ["--hub-host", str(args.hub_host).strip()]
        if str(getattr(args, "ssh_key", "") or "").strip():
            argv2 += ["--ssh-key", str(args.ssh_key).strip()]
        env2 = None
        if bool(getattr(args, "password_stdin", False)):
            argv2.append("--password-stdin")
            env2 = dict(os.environ)
            env2["TEAMOS_SSH_PASSWORD"] = stdin_password
        _approval_gate(
            args,
            repo_root=repo_root,
            action_kind="hub_push_config_with_secrets",
            summary=f"node add push hub config host={args.host} user={args.user}",
            payload={"host": args.host, "user": args.user, "remote_env_path": str(getattr(args, 'remote_env_path', '') or '~/.teamos/node.env')},
        )
        hub_push_script = (repo_root / "scripts" / "pipelines" / "hub_push_config.py").resolve()
        if not hub_push_script.exists():
            raise RuntimeError(f"missing pipeline: {hub_push_script}")
        p2 = subprocess.run(
            [sys.executable, str(hub_push_script)] + argv2,
            check=False,
            env=env2 or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        sys.stdout.write(p2.stdout or "")
        sys.stderr.write(p2.stderr or "")
        stage = _extract_stage_from_json_output(str(p2.stdout or ""), default="hub_push_config")
        _record_installer_run(
            repo_root=repo_root,
            workspace_root=ws_root,
            component="node_add.push_hub_config",
            stage=stage,
            target_host=str(args.host),
            ok=(p2.returncode == 0),
            stdout_text=str(p2.stdout or ""),
            stderr_text=str(p2.stderr or ""),
        )
        if p2.returncode != 0:
            raise SystemExit(p2.returncode)
