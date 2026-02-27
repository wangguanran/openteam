#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket

import locks

from _common import PipelineError, add_default_args, resolve_repo_root
from hub_common import (
    enforce_hub_env_config_security,
    hub_compose_path,
    hub_root,
    load_hub_env_required,
    render_compose,
    render_pg_hba,
    run_compose,
    validate_hub_compose_required,
    write_json_stdout,
    write_secure_file,
)


def _cidrs(raw: str) -> list[str]:
    out: list[str] = []
    for s in str(raw or "").split(","):
        c = s.strip()
        if c:
            out.append(c)
    return out


def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Expose Team-OS hub to selected CIDRs (HIGH risk)")
    add_default_args(ap)
    ap.add_argument("--bind-ip", required=True)
    ap.add_argument("--allow-cidrs", required=True)
    ap.add_argument("--open-postgres", action="store_true", default=True)
    ap.add_argument("--open-redis", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    bind_ip = str(args.bind_ip or "").strip()
    if not bind_ip:
        raise PipelineError("--bind-ip is required")
    if bind_ip == "0.0.0.0":
        raise PipelineError("bind_ip=0.0.0.0 is forbidden by default")

    repo = resolve_repo_root(args)
    hub = hub_root()
    env_path = hub / "env" / ".env"
    env = load_hub_env_required(hub)
    validate_hub_compose_required(hub)
    enforce_hub_env_config_security(hub)

    allow = _cidrs(str(args.allow_cidrs or ""))
    if not allow:
        raise PipelineError("--allow-cidrs must not be empty")

    repo_lock = None
    hub_lock = None
    cluster_lock = None
    try:
        if not args.dry_run:
            hub_lock = locks.acquire_hub_lock(task_id=str(env.get("TEAMOS_TASK_ID") or ""))
            cluster_lock = locks.acquire_cluster_lock(repo_root=repo, task_id=str(env.get("TEAMOS_TASK_ID") or ""))

            env["PG_BIND_IP"] = bind_ip if bool(args.open_postgres) else "127.0.0.1"
            env["REDIS_BIND_IP"] = bind_ip if bool(args.open_redis) else "127.0.0.1"
            write_secure_file(env_path, "\n".join([f"{k}={env[k]}" for k in sorted(env.keys())]) + "\n", mode=0o600)

            compose_text = render_compose(
                repo_root=repo,
                pg_bind_ip=str(env.get("PG_BIND_IP") or "127.0.0.1"),
                pg_port=int(str(env.get("PG_PORT") or "5432")),
                redis_bind_ip=str(env.get("REDIS_BIND_IP") or "127.0.0.1"),
                redis_port=int(str(env.get("REDIS_PORT") or "6379")),
            )
            write_secure_file(hub_compose_path(hub), compose_text, mode=0o600)

            pg_hba = render_pg_hba(repo_root=repo, allow_cidrs=allow if bool(args.open_postgres) else [])
            write_secure_file(hub / "config" / "postgres" / "pg_hba.conf", pg_hba, mode=0o600)

            fw_plan = hub / "FIREWALL_PLAN.md"
            plan_lines = [
                "# Firewall Plan (Generated)",
                "",
                f"- bind_ip: {bind_ip}",
                f"- allow_cidrs: {', '.join(allow)}",
                f"- open_postgres: {bool(args.open_postgres)}",
                f"- open_redis: {bool(args.open_redis)}",
                "",
                "## Suggested commands (review before applying)",
            ]
            if bool(args.open_postgres):
                plan_lines.append(f"- ufw allow from <cidr> to {bind_ip} port {env.get('PG_PORT','5432')} proto tcp")
            if bool(args.open_redis):
                plan_lines.append(f"- ufw allow from <cidr> to {bind_ip} port {env.get('REDIS_PORT','6379')} proto tcp")
            write_secure_file(fw_plan, "\n".join(plan_lines).rstrip() + "\n", mode=0o600)
            enforce_hub_env_config_security(hub)

            run_compose(hub=hub, args=["up", "-d", "--force-recreate"], capture=False)

        out = {
            "ok": True,
            "dry_run": bool(args.dry_run),
            "hub_root": str(hub),
            "bind_ip": bind_ip,
            "allow_cidrs": allow,
            "open_postgres": bool(args.open_postgres),
            "open_redis": bool(args.open_redis),
            "firewall_plan": str(hub / "FIREWALL_PLAN.md"),
            "postgres_tcp_open": _tcp_open(str(env.get("PG_BIND_IP") or bind_ip), int(str(env.get("PG_PORT") or "5432"))),
            "redis_tcp_open": _tcp_open(str(env.get("REDIS_BIND_IP") or bind_ip), int(str(env.get("REDIS_PORT") or "6379"))),
        }
        write_json_stdout(out)
        return 0
    finally:
        locks.release_lock(cluster_lock)
        locks.release_lock(hub_lock)
        locks.release_lock(repo_lock)


if __name__ == "__main__":
    raise SystemExit(main())
