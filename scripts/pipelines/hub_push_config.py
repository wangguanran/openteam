#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from _common import PipelineError, add_default_args
from hub_common import (
    enforce_hub_env_config_security,
    ensure_dir_secure,
    hub_root,
    load_hub_env_required,
    write_json_stdout,
    write_secure_file,
)


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

    hub = hub_root()
    env_local = load_hub_env_required(hub)
    enforce_hub_env_config_security(hub)

    hub_host = str(args.hub_host or "").strip() or str(env_local.get("PG_BIND_IP") or "127.0.0.1")
    pg_port = str(env_local.get("PG_PORT") or "5432")
    pg_user = str(env_local.get("POSTGRES_USER") or "teamos")
    pg_pwd = str(env_local.get("POSTGRES_PASSWORD") or "")
    pg_db = str(env_local.get("POSTGRES_DB") or "teamos")

    redis_port = str(env_local.get("REDIS_PORT") or "6379")
    redis_pwd = str(env_local.get("REDIS_PASSWORD") or "")

    db_url = f"postgresql://{pg_user}:{pg_pwd}@{hub_host}:{pg_port}/{pg_db}"
    redis_url = f"redis://:{redis_pwd}@{hub_host}:{redis_port}/0"

    remote_text_lines = [
        f"TEAMOS_DB_URL={db_url}",
        f"TEAMOS_REDIS_URL={redis_url}",
        f"TEAMOS_HUB_HOST={hub_host}",
        "TEAMOS_HUB_REDIS_ENABLED=1",
    ]
    remote_text = "\n".join(remote_text_lines).rstrip() + "\n"

    if args.dry_run:
        write_json_stdout({"ok": True, "dry_run": True, "host": args.host, "user": args.user, "remote_env_path": args.remote_env_path, "redis_enabled": True})
        return 0

    tmp_dir = hub / "state" / "tmp"
    ensure_dir_secure(tmp_dir)
    _fd, temp_name = tempfile.mkstemp(prefix="push_config_", suffix=".env", dir=str(tmp_dir), text=True)
    os.close(_fd)
    temp_path = Path(temp_name)
    write_secure_file(temp_path, remote_text, mode=0o600)

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

        p1 = subprocess.run(scp_cmd + [str(temp_path), f"{target}:{remote_tmp}"], env=env_exec, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if p1.returncode != 0:
            write_json_stdout({"ok": False, "stage": "scp", "stderr": (p1.stderr or "")[-1000:]})
            return 2

        remote_path = str(args.remote_env_path)
        cmd_remote = f"mkdir -p $(dirname {remote_path}) && install -m 600 {remote_tmp} {remote_path} && rm -f {remote_tmp}"
        p2 = subprocess.run(ssh_cmd + [target, cmd_remote], env=env_exec, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if p2.returncode != 0:
            write_json_stdout({"ok": False, "stage": "ssh", "stderr": (p2.stderr or "")[-1000:]})
            return 2

        write_json_stdout({"ok": True, "host": args.host, "remote_env_path": remote_path, "redis_enabled": True})
        return 0
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
