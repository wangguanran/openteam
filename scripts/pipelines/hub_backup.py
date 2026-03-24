#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import PipelineError, add_default_args, ts_compact_utc
from hub_common import (
    enforce_hub_env_config_security,
    ensure_dir_secure,
    hub_root,
    load_hub_env_required,
    run_compose,
    validate_hub_compose_required,
    validate_hub_runtime_path,
    write_json_stdout,
    write_secure_file,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backup Team-OS hub Postgres")
    add_default_args(ap)
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)

    hub = hub_root()
    try:
        env = load_hub_env_required(hub)
        validate_hub_compose_required(hub)
        enforce_hub_env_config_security(hub)
    except PipelineError as e:
        write_json_stdout({"ok": False, "error": str(e), "hint": "run openteam hub init"})
        return 2

    if str(args.output or "").strip():
        out_path = Path(str(args.output)).expanduser().resolve()
    else:
        out_path = hub / "backups" / f"hub_{ts_compact_utc()}.sql"
    validate_hub_runtime_path(out_path, hub=hub, label="backup output")
    ensure_dir_secure(out_path.parent)

    user = str(env.get("POSTGRES_USER") or "openteam")
    db = str(env.get("POSTGRES_DB") or "openteam")
    dump = run_compose(hub=hub, args=["exec", "-T", "postgres", "pg_dump", "-U", user, "-d", db], capture=True)
    if not dump.get("ok"):
        write_json_stdout({"ok": False, "stderr": dump.get("stderr", "")[-1000:]})
        return 2

    write_secure_file(out_path, str(dump.get("stdout") or ""), mode=0o600)
    enforce_hub_env_config_security(hub)
    write_json_stdout({"ok": True, "output": str(out_path), "bytes": out_path.stat().st_size})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
