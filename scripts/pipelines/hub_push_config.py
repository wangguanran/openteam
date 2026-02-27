#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from _common import PipelineError, add_default_args
from hub_common import hub_env_path, hub_root, parse_env_file, write_json_stdout


def _ssh_base(user: str, host: str, ssh_key: str = "", use_password_stdin: bool = False, password: str = "") -> tuple[list[str], dict[str, str]]:
    env = dict(os.environ)
    prefix: list[str] = []
    if use_password_stdin:
        if not password:
            raise PipelineError("--password-stdin requested but no stdin password was provided")
        prefix = ["sshpass", "-e"]
        env["SSHPASS"] = password
    ssh = ["ssh"]
    scp = ["scp"]
    if ssh_key:
        ssh += ["-i", ssh_key]
        scp += ["-i", ssh_key]
    target = f"{user}@{host}"
    return (prefix + ssh + [target], env)


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
            raise PipelineError("missing TEAMOS_SSH_PASSWORD env for --password-stdin mode")

    env_local = parse_env_file(hub_env_path(hub_root()))
    if not env_local:
        raise PipelineError("missing local hub env (run teamos hub init)")

    hub_host = str(args.hub_host or "").strip() or str(env_local.get("PG_BIND_IP") or "127.0.0.1")
    pg_port = str(env_local.get("PG_PORT") or "5432")
    pg_user = str(env_local.get("POSTGRES_USER") or "teamos")
    pg_pwd = str(env_local.get("POSTGRES_PASSWORD") or "")
    pg_db = str(env_local.get("POSTGRES_DB") or "teamos")

    redis_enabled = str(env_local.get("HUB_REDIS_ENABLED") or "1") == "1"
    redis_port = str(env_local.get("REDIS_PORT") or "6379")
    redis_pwd = str(env_local.get("REDIS_PASSWORD") or "")

    db_url = f"postgresql://{pg_user}:{pg_pwd}@{hub_host}:{pg_port}/{pg_db}"
    redis_url = f"redis://:{redis_pwd}@{hub_host}:{redis_port}/0" if redis_enabled else ""

    remote_text_lines = [
        f"TEAMOS_DB_URL={db_url}",
        f"TEAMOS_REDIS_URL={redis_url}",
        f"TEAMOS_HUB_HOST={hub_host}",
    ]
    remote_text = "\n".join(remote_text_lines).rstrip() + "\n"

    if args.dry_run:
        write_json_stdout({"ok": True, "dry_run": True, "host": args.host, "user": args.user, "remote_env_path": args.remote_env_path, "redis_enabled": redis_enabled})
        return 0

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
        tf.write(remote_text)
        temp_path = tf.name

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
        remote_tmp = "/tmp/teamos_node_env"

        p1 = subprocess.run(scp_cmd + [temp_path, f"{target}:{remote_tmp}"], env=env_exec, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if p1.returncode != 0:
            write_json_stdout({"ok": False, "stage": "scp", "stderr": (p1.stderr or "")[-1000:]})
            return 2

        remote_path = str(args.remote_env_path)
        cmd_remote = f"mkdir -p $(dirname {remote_path}) && install -m 600 {remote_tmp} {remote_path} && rm -f {remote_tmp}"
        p2 = subprocess.run(ssh_cmd + [target, cmd_remote], env=env_exec, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if p2.returncode != 0:
            write_json_stdout({"ok": False, "stage": "ssh", "stderr": (p2.stderr or "")[-1000:]})
            return 2

        write_json_stdout({"ok": True, "host": args.host, "remote_env_path": remote_path, "redis_enabled": redis_enabled})
        return 0
    finally:
        try:
            Path(temp_path).unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
