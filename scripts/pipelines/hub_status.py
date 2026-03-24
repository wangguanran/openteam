#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket

from _common import PipelineError, add_default_args
from hub_common import (
    enforce_hub_env_config_security,
    hub_root,
    load_hub_env_required,
    run_compose,
    validate_hub_compose_required,
    write_json_stdout,
)


def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Show Team-OS hub status")
    add_default_args(ap)
    args = ap.parse_args(argv)

    hub = hub_root()
    try:
        env = load_hub_env_required(hub)
        validate_hub_compose_required(hub)
        enforce_hub_env_config_security(hub)
    except PipelineError as e:
        write_json_stdout({"ok": False, "error": str(e), "hint": "run: openteam hub init"})
        return 2

    ps = run_compose(hub=hub, args=["ps"], capture=True)

    pg_host = str(env.get("PG_BIND_IP") or "127.0.0.1")
    pg_port = int(str(env.get("PG_PORT") or "5432"))
    redis_host = str(env.get("REDIS_BIND_IP") or "127.0.0.1")
    redis_port = int(str(env.get("REDIS_PORT") or "6379"))

    out = {
        "ok": True,
        "hub_root": str(hub),
        "compose_ok": bool(ps.get("ok")),
        "compose_ps": (ps.get("stdout") or "")[-5000:],
        "postgres": {
            "bind_ip": pg_host,
            "port": pg_port,
            "tcp_open": _tcp_open(pg_host, pg_port),
        },
        "redis": {
            "enabled": True,
            "bind_ip": redis_host,
            "port": redis_port,
            "tcp_open": _tcp_open(redis_host, redis_port),
        },
    }
    write_json_stdout(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
