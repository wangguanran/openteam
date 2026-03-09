#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from _common import PipelineError, add_default_args, resolve_repo_root
from hub_common import (
    enforce_hub_env_config_security,
    ensure_dir_secure,
    hub_root,
    load_hub_env_required,
    write_json_stdout,
    write_secure_file,
)


def _tail(text: str, *, max_chars: int = 1000) -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def _stage_fail(*, stage: str, stderr: str, extra: dict[str, object] | None = None) -> int:
    out: dict[str, object] = {"ok": False, "stage": str(stage), "stderr": _tail(stderr)}
    if extra:
        out.update(extra)
    write_json_stdout(out)
    return 2


def _read_required_text(path: Path) -> str:
    if not path.exists():
        raise PipelineError(f"missing required file: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _remote_home_path(path: str) -> str:
    p = str(path or "").strip()
    if p.startswith("~/"):
        return "$HOME/" + p[2:]
    if p == "~":
        return "$HOME"
    return p


def _dq(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Push Brain hub DB/Redis config (with secrets) to a remote node")
    add_default_args(ap)
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--ssh-key", default="")
    ap.add_argument("--password-stdin", action="store_true")
    ap.add_argument("--remote-env-path", default="~/.teamos/node.env")
    ap.add_argument("--hub-host", default="", help="override advertised hub host/ip")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    password = ""
    if bool(args.password_stdin):
        password = (os.getenv("TEAMOS_SSH_PASSWORD") or "").strip()
        if not password:
            return _stage_fail(stage="prepare", stderr="missing TEAMOS_SSH_PASSWORD env for --password-stdin mode")

    repo = resolve_repo_root(args)
    hub = hub_root()
    try:
        env_local = load_hub_env_required(hub)
        enforce_hub_env_config_security(hub)
    except PipelineError as e:
        return _stage_fail(stage="prepare", stderr=str(e))

    central_allowlist_src = repo / "specs" / "policies" / "central_model_allowlist.yaml"
    approvals_policy_src = repo / "specs" / "policies" / "approvals.yaml"
    try:
        central_allowlist_text = _read_required_text(central_allowlist_src)
        approvals_policy_text = _read_required_text(approvals_policy_src)
    except PipelineError as e:
        return _stage_fail(stage="prepare", stderr=str(e))

    hub_host = str(args.hub_host or "").strip() or str(env_local.get("PG_BIND_IP") or "127.0.0.1")
    pg_port = str(env_local.get("PG_PORT") or "5432")
    pg_user = str(env_local.get("POSTGRES_USER") or "teamos")
    pg_pwd = str(env_local.get("POSTGRES_PASSWORD") or "")
    pg_db = str(env_local.get("POSTGRES_DB") or "teamos")

    redis_port = str(env_local.get("REDIS_PORT") or "6379")
    redis_pwd = str(env_local.get("REDIS_PASSWORD") or "")

    db_url = f"postgresql://{pg_user}:{pg_pwd}@{hub_host}:{pg_port}/{pg_db}"
    redis_url = f"redis://:{redis_pwd}@{hub_host}:{redis_port}/0"
    remote_env_path = str(args.remote_env_path or "~/.teamos/node.env")
    remote_policy_dir = "~/.teamos/policies"
    remote_allowlist_path = f"{remote_policy_dir}/central_model_allowlist.yaml"
    remote_approvals_path = f"{remote_policy_dir}/approvals.yaml"

    remote_text_lines = [
        f"TEAMOS_DB_URL={db_url}",
        f"TEAMOS_REDIS_URL={redis_url}",
        f"TEAMOS_HUB_HOST={hub_host}",
        "TEAMOS_HUB_REDIS_ENABLED=1",
        f"TEAMOS_CENTRAL_MODEL_ALLOWLIST_PATH={remote_allowlist_path}",
        f"TEAMOS_APPROVALS_POLICY_PATH={remote_approvals_path}",
    ]
    remote_text = "\n".join(remote_text_lines).rstrip() + "\n"

    if args.dry_run:
        write_json_stdout(
            {
                "ok": True,
                "dry_run": True,
                "host": args.host,
                "user": args.user,
                "remote_env_path": remote_env_path,
                "remote_policy_dir": remote_policy_dir,
                "remote_allowlist_path": remote_allowlist_path,
                "remote_approvals_path": remote_approvals_path,
                "remote_env_vars": {
                    "TEAMOS_CENTRAL_MODEL_ALLOWLIST_PATH": remote_allowlist_path,
                    "TEAMOS_APPROVALS_POLICY_PATH": remote_approvals_path,
                },
                "policy_sources": {
                    "central_model_allowlist": str(central_allowlist_src),
                    "approvals": str(approvals_policy_src),
                },
                "redis_enabled": True,
            }
        )
        return 0

    tmp_dir = hub / "state" / "tmp"
    ensure_dir_secure(tmp_dir)
    _fd_env, temp_env_name = tempfile.mkstemp(prefix="push_config_", suffix=".env", dir=str(tmp_dir), text=True)
    _fd_allow, temp_allow_name = tempfile.mkstemp(prefix="push_allowlist_", suffix=".yaml", dir=str(tmp_dir), text=True)
    _fd_appr, temp_appr_name = tempfile.mkstemp(prefix="push_approvals_", suffix=".yaml", dir=str(tmp_dir), text=True)
    os.close(_fd_env)
    os.close(_fd_allow)
    os.close(_fd_appr)
    temp_env_path = Path(temp_env_name)
    temp_allow_path = Path(temp_allow_name)
    temp_appr_path = Path(temp_appr_name)
    write_secure_file(temp_env_path, remote_text, mode=0o600)
    write_secure_file(temp_allow_path, central_allowlist_text, mode=0o600)
    write_secure_file(temp_appr_path, approvals_policy_text, mode=0o600)

    try:
        ssh_key = str(args.ssh_key or "").strip()
        env_exec = dict(os.environ)
        scp_cmd = ["scp"]
        ssh_cmd = ["ssh"]
        if bool(args.password_stdin):
            scp_cmd = ["sshpass", "-e"] + scp_cmd
            ssh_cmd = ["sshpass", "-e"] + ssh_cmd
            env_exec["SSHPASS"] = password
        if ssh_key:
            scp_cmd += ["-i", ssh_key]
            ssh_cmd += ["-i", ssh_key]

        target = f"{args.user}@{args.host}"
        remote_tmp_env = "/tmp/teamos_node_env"
        remote_tmp_allow = "/tmp/teamos_central_model_allowlist.yaml"
        remote_tmp_appr = "/tmp/teamos_approvals.yaml"

        p1 = subprocess.run(
            scp_cmd + [str(temp_env_path), f"{target}:{remote_tmp_env}"],
            env=env_exec,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if p1.returncode != 0:
            return _stage_fail(stage="scp_env", stderr=str(p1.stderr or ""))

        p2 = subprocess.run(
            scp_cmd + [str(temp_allow_path), f"{target}:{remote_tmp_allow}"],
            env=env_exec,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if p2.returncode != 0:
            return _stage_fail(stage="scp_allowlist", stderr=str(p2.stderr or ""))

        p3 = subprocess.run(
            scp_cmd + [str(temp_appr_path), f"{target}:{remote_tmp_appr}"],
            env=env_exec,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if p3.returncode != 0:
            return _stage_fail(stage="scp_approvals", stderr=str(p3.stderr or ""))

        remote_env_path_shell = _remote_home_path(remote_env_path)
        remote_policy_dir_shell = _remote_home_path(remote_policy_dir)
        remote_allowlist_shell = _remote_home_path(remote_allowlist_path)
        remote_approvals_shell = _remote_home_path(remote_approvals_path)
        cmd_remote = (
            f"REMOTE_ENV={_dq(remote_env_path_shell)}; "
            f"REMOTE_POLICIES={_dq(remote_policy_dir_shell)}; "
            f"REMOTE_ALLOWLIST={_dq(remote_allowlist_shell)}; "
            f"REMOTE_APPROVALS={_dq(remote_approvals_shell)}; "
            f"mkdir -p \"$(dirname \"$REMOTE_ENV\")\" \"$REMOTE_POLICIES\" "
            f"&& install -m 600 {shlex.quote(remote_tmp_env)} \"$REMOTE_ENV\" "
            f"&& install -m 600 {shlex.quote(remote_tmp_allow)} \"$REMOTE_ALLOWLIST\" "
            f"&& install -m 600 {shlex.quote(remote_tmp_appr)} \"$REMOTE_APPROVALS\" "
            f"&& rm -f {shlex.quote(remote_tmp_env)} {shlex.quote(remote_tmp_allow)} {shlex.quote(remote_tmp_appr)}"
        )
        p4 = subprocess.run(
            ssh_cmd + [target, cmd_remote],
            env=env_exec,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if p4.returncode != 0:
            return _stage_fail(stage="ssh_install", stderr=str(p4.stderr or ""))

        write_json_stdout(
            {
                "ok": True,
                "host": args.host,
                "remote_env_path": remote_env_path,
                "remote_policy_dir": remote_policy_dir,
                "remote_allowlist_path": remote_allowlist_path,
                "remote_approvals_path": remote_approvals_path,
                "redis_enabled": True,
            }
        )
        return 0
    finally:
        try:
            temp_env_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            temp_allow_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            temp_appr_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
