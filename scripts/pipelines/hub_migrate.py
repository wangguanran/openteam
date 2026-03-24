#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import PipelineError, add_default_args, resolve_repo_root
from _db import connect
from db_migrate import apply_migrations
from hub_common import (
    enforce_hub_env_config_security,
    hub_root,
    load_hub_env_required,
    local_db_dsn,
    validate_hub_compose_required,
    write_json_stdout,
)


def _list_migrations(repo_root):
    mig_dir = repo_root / "tooling" / "migrations"
    out: list[tuple[str, object]] = []
    for p in sorted(mig_dir.glob("*.sql")):
        name = p.name
        if len(name) >= 4 and name[:4].isdigit():
            out.append((name[:4], p))
    return out


def _ensure_target_db_exists(*, env: dict[str, str]) -> None:
    user = str(env.get("POSTGRES_USER") or "openteam")
    pwd = str(env.get("POSTGRES_PASSWORD") or "")
    db = str(env.get("POSTGRES_DB") or "openteam")
    bind_ip = str(env.get("PG_BIND_IP") or "127.0.0.1")
    port = int(str(env.get("PG_PORT") or "5432"))
    admin_dsn = f"postgresql://{user}:{pwd}@{bind_ip}:{port}/postgres"
    conn = connect(admin_dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM pg_database WHERE datname = %s
                """,
                (db,),
            )
            row = cur.fetchone()
            if row:
                return
            cur.execute(f'CREATE DATABASE \"{db}\"')
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply Team-OS DB migrations to local hub Postgres")
    add_default_args(ap)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    hub = hub_root()
    env = load_hub_env_required(hub)
    validate_hub_compose_required(hub)
    enforce_hub_env_config_security(hub)

    migrations = _list_migrations(repo)
    if not migrations:
        raise PipelineError("no migrations found")
    if args.dry_run:
        write_json_stdout({"ok": True, "dry_run": True, "migrations": [m[0] for m in migrations], "hub_root": str(hub)})
        return 0

    dsn = local_db_dsn(env)
    try:
        conn = connect(dsn)
    except Exception as e:
        msg = str(e)
        if "does not exist" in msg and "database" in msg:
            _ensure_target_db_exists(env=env)
            conn = connect(dsn)
        else:
            raise
    try:
        out = apply_migrations(conn, migrations)
    finally:
        conn.close()

    out["hub_root"] = str(hub)
    write_json_stdout(out)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
