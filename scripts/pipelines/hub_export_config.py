#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from _common import PipelineError, add_default_args
from hub_common import enforce_hub_env_config_security, hub_root, load_hub_env_required


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export non-secret hub config for remote nodes")
    add_default_args(ap)
    ap.add_argument("--format", choices=["env", "yaml"], default="env")
    args = ap.parse_args(argv)

    hub = hub_root()
    try:
        env = load_hub_env_required(hub)
        enforce_hub_env_config_security(hub)
    except PipelineError as e:
        print(json.dumps({"ok": False, "error": str(e), "hint": "run teamos hub init"}, ensure_ascii=False, indent=2))
        return 2

    host = str(env.get("PG_BIND_IP") or "127.0.0.1")
    pg_port = str(env.get("PG_PORT") or "5432")
    pg_user = str(env.get("POSTGRES_USER") or "teamos")
    pg_db = str(env.get("POSTGRES_DB") or "teamos")

    redis_host = str(env.get("REDIS_BIND_IP") or "127.0.0.1")
    redis_port = str(env.get("REDIS_PORT") or "6379")

    model = {
        "TEAMOS_DB_URL_TEMPLATE": f"postgresql://{pg_user}:<password>@{host}:{pg_port}/{pg_db}",
        "TEAMOS_REDIS_URL_TEMPLATE": f"redis://:<password>@{redis_host}:{redis_port}/0",
        "TEAMOS_HUB_HOST": host,
        "TEAMOS_HUB_PG_PORT": pg_port,
        "TEAMOS_HUB_REDIS_ENABLED": "1",
        "TEAMOS_HUB_REDIS_PORT": redis_port,
    }

    if args.format == "env":
        for k in sorted(model.keys()):
            print(f"{k}={model[k]}")
    else:
        print("hub:")
        for k in sorted(model.keys()):
            print(f"  {k}: \"{model[k]}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
