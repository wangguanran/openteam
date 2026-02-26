#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import PipelineError, add_default_args, resolve_repo_root
from _db import connect
from db_migrate import apply_migrations
from hub_common import hub_env_path, hub_root, local_db_dsn, parse_env_file, write_json_stdout


def _list_migrations(repo_root):
    mig_dir = repo_root / ".team-os" / "db" / "migrations"
    out: list[tuple[str, object]] = []
    for p in sorted(mig_dir.glob("*.sql")):
        name = p.name
        if len(name) >= 4 and name[:4].isdigit():
            out.append((name[:4], p))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply Team-OS DB migrations to local hub Postgres")
    add_default_args(ap)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    hub = hub_root()
    env = parse_env_file(hub_env_path(hub))
    if not env:
        raise PipelineError(f"missing hub env: {hub_env_path(hub)} (run teamos hub init)")

    migrations = _list_migrations(repo)
    if not migrations:
        raise PipelineError("no migrations found")
    if args.dry_run:
        write_json_stdout({"ok": True, "dry_run": True, "migrations": [m[0] for m in migrations], "hub_root": str(hub)})
        return 0

    dsn = local_db_dsn(env)
    conn = connect(dsn)
    try:
        out = apply_migrations(conn, migrations)
    finally:
        conn.close()

    out["hub_root"] = str(hub)
    write_json_stdout(out)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
