#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from _common import PipelineError, add_default_args
from hub_common import (
    enforce_hub_env_config_security,
    hub_root,
    load_hub_env_required,
    validate_hub_compose_required,
    validate_hub_runtime_path,
    write_json_stdout,
)


def _compose_cmd(hub: Path) -> list[str]:
    from hub_common import _docker_compose_cmd  # type: ignore

    return _docker_compose_cmd() + ["-f", str(hub / "compose" / "docker-compose.yml")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Restore Team-OS hub Postgres from sql file (HIGH risk)")
    add_default_args(ap)
    ap.add_argument("--file", required=True)
    args = ap.parse_args(argv)

    hub = hub_root()
    env = load_hub_env_required(hub)
    validate_hub_compose_required(hub)
    enforce_hub_env_config_security(hub)

    src = Path(str(args.file)).expanduser().resolve()
    if not src.exists():
        raise PipelineError(f"backup file not found: {src}")
    validate_hub_runtime_path(src, hub=hub, label="restore input")

    user = str(env.get("POSTGRES_USER") or "openteam")
    db = str(env.get("POSTGRES_DB") or "openteam")
    cmd = _compose_cmd(hub) + ["exec", "-T", "postgres", "psql", "-U", user, "-d", db]

    with src.open("rb") as f:
        p = subprocess.run(cmd, cwd=str(hub / "compose"), stdin=f, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    write_json_stdout(
        {
            "ok": p.returncode == 0,
            "file": str(src),
            "stdout": (p.stdout or b"").decode("utf-8", errors="replace")[-2000:],
            "stderr": (p.stderr or b"").decode("utf-8", errors="replace")[-2000:],
        }
    )
    return 0 if p.returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
