#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import add_default_args, ts_compact_utc
from hub_common import hub_env_path, hub_root, parse_env_file, run_compose, write_json_stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backup Team-OS hub Postgres")
    add_default_args(ap)
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)

    hub = hub_root()
    env = parse_env_file(hub_env_path(hub))
    if not env:
        write_json_stdout({"ok": False, "error": "missing hub env", "hint": "run teamos hub init"})
        return 2

    out_path = Path(str(args.output or "")).expanduser().resolve() if str(args.output or "").strip() else (hub / "backups" / f"hub_{ts_compact_utc()}.sql")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    user = str(env.get("POSTGRES_USER") or "teamos")
    db = str(env.get("POSTGRES_DB") or "teamos")
    dump = run_compose(hub=hub, args=["exec", "-T", "postgres", "pg_dump", "-U", user, "-d", db], capture=True)
    if not dump.get("ok"):
        write_json_stdout({"ok": False, "stderr": dump.get("stderr", "")[-1000:]})
        return 2

    out_path.write_text(str(dump.get("stdout") or ""), encoding="utf-8")
    write_json_stdout({"ok": True, "output": str(out_path), "bytes": out_path.stat().st_size})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
