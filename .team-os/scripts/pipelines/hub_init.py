#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from _common import PipelineError, add_default_args, resolve_repo_root
from hub_common import (
    connection_info_md,
    ensure_dir_secure,
    format_env,
    hub_compose_path,
    hub_env_path,
    hub_root,
    parse_env_file,
    random_secret,
    render_compose,
    render_hub_readme,
    render_pg_hba,
    write_json_stdout,
    write_secure_file,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Initialize local Team-OS Hub (Postgres + optional Redis)")
    add_default_args(ap)
    ap.add_argument("--with-redis", action="store_true", help="enable redis (default: enabled)")
    ap.add_argument("--without-redis", action="store_true", help="disable redis")
    ap.add_argument("--pg-port", type=int, default=5432)
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    hub = hub_root()

    with_redis = True
    if bool(args.without_redis):
        with_redis = False
    elif bool(args.with_redis):
        with_redis = True

    old_umask = os.umask(0o077)
    try:
        dirs = [
            hub / "compose",
            hub / "env",
            hub / "data" / "pgdata",
            hub / "data" / "redisdata",
            hub / "config" / "postgres",
            hub / "backups",
            hub / "logs",
            hub / "state" / "locks",
        ]
        if not args.dry_run:
            for d in dirs:
                ensure_dir_secure(d)

            env_path = hub_env_path(hub)
            env = parse_env_file(env_path)
            env.setdefault("POSTGRES_DB", "teamos")
            env.setdefault("POSTGRES_USER", "teamos")
            env.setdefault("POSTGRES_PASSWORD", random_secret())
            env.setdefault("REDIS_PASSWORD", random_secret())
            env["PG_BIND_IP"] = "127.0.0.1"
            env["PG_PORT"] = str(int(args.pg_port))
            env["HUB_REDIS_ENABLED"] = "1" if with_redis else "0"
            env["REDIS_BIND_IP"] = "127.0.0.1"
            env["REDIS_PORT"] = str(int(args.redis_port))
            write_secure_file(env_path, format_env(env), mode=0o600)

            compose = render_compose(
                repo_root=repo,
                pg_bind_ip=env["PG_BIND_IP"],
                pg_port=int(env["PG_PORT"]),
                redis_enabled=(env.get("HUB_REDIS_ENABLED") == "1"),
                redis_bind_ip=env["REDIS_BIND_IP"],
                redis_port=int(env["REDIS_PORT"]),
            )
            write_secure_file(hub_compose_path(hub), compose, mode=0o600)

            pg_hba = render_pg_hba(repo_root=repo, allow_cidrs=[])
            write_secure_file(hub / "config" / "postgres" / "pg_hba.conf", pg_hba, mode=0o600)

            write_secure_file(hub / "README.md", render_hub_readme(
                repo_root=repo,
                hub=hub,
                pg_bind_ip=env["PG_BIND_IP"],
                pg_port=int(env["PG_PORT"]),
                redis_enabled=(env.get("HUB_REDIS_ENABLED") == "1"),
                redis_bind_ip=env["REDIS_BIND_IP"],
                redis_port=int(env["REDIS_PORT"]),
            ), mode=0o600)
            write_secure_file(hub / "CONNECTION_INFO.md", connection_info_md(env), mode=0o600)

        write_json_stdout(
            {
                "ok": True,
                "dry_run": bool(args.dry_run),
                "hub_root": str(hub),
                "postgres": {"bind_ip": "127.0.0.1", "port": int(args.pg_port)},
                "redis": {"enabled": with_redis, "bind_ip": "127.0.0.1", "port": int(args.redis_port)},
            }
        )
        return 0
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())
