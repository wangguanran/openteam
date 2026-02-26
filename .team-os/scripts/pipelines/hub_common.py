#!/usr/bin/env python3
from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

from _common import PipelineError, read_text, render_template, write_text


def hub_root() -> Path:
    return (Path.home() / ".teamos" / "hub").resolve()


def ensure_dir_secure(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass


def write_secure_file(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[str(k).strip()] = str(v).strip()
    return out


def format_env(data: dict[str, str]) -> str:
    keys = sorted([k for k in data.keys() if str(k).strip()])
    lines = [f"{k}={data[k]}" for k in keys]
    return "\n".join(lines).rstrip() + "\n"


def random_secret() -> str:
    # URL-safe and deterministic length envelope.
    return secrets.token_urlsafe(32)


def load_template(repo_root: Path, rel: str) -> str:
    p = repo_root / rel
    if not p.exists():
        raise PipelineError(f"missing template: {p}")
    return read_text(p)


def render_compose(*, repo_root: Path, pg_bind_ip: str, pg_port: int, redis_enabled: bool, redis_bind_ip: str, redis_port: int) -> str:
    tpl = load_template(repo_root, ".team-os/templates/hub/docker-compose.yml.j2")
    redis_block = ""
    if redis_enabled:
        redis_block = "\n".join(
            [
                "  redis:",
                "    image: redis:7",
                "    container_name: teamos-hub-redis",
                "    restart: unless-stopped",
                "    env_file:",
                "      - ../env/.env",
                "    command: [\"redis-server\", \"--appendonly\", \"yes\", \"--requirepass\", \"$${REDIS_PASSWORD}\"]",
                "    ports:",
                f"      - \"{redis_bind_ip}:{int(redis_port)}:6379\"",
                "    volumes:",
                "      - ../data/redisdata:/data",
                "    healthcheck:",
                "      test: [\"CMD-SHELL\", \"redis-cli -a $${REDIS_PASSWORD} ping | grep PONG\"]",
                "      interval: 10s",
                "      timeout: 5s",
                "      retries: 10",
            ]
        )
    return render_template(
        tpl,
        {
            "PG_BIND_IP": str(pg_bind_ip),
            "PG_PORT": str(int(pg_port)),
            "REDIS_BLOCK": redis_block,
        },
    )


def render_pg_hba(*, repo_root: Path, allow_cidrs: list[str]) -> str:
    tpl = load_template(repo_root, ".team-os/templates/hub/pg_hba.conf.j2")
    rules: list[str] = []
    for cidr in allow_cidrs:
        c = str(cidr or "").strip()
        if not c:
            continue
        rules.append(f"host    all             all             {c:<22} scram-sha-256")
    return render_template(tpl, {"ALLOW_RULES": "\n".join(rules)})


def render_hub_readme(*, repo_root: Path, hub: Path, pg_bind_ip: str, pg_port: int, redis_enabled: bool, redis_bind_ip: str, redis_port: int) -> str:
    tpl = load_template(repo_root, ".team-os/templates/hub/README.md.j2")
    return render_template(
        tpl,
        {
            "HUB_ROOT": str(hub),
            "PG_BIND_IP": str(pg_bind_ip),
            "PG_PORT": str(int(pg_port)),
            "REDIS_ENABLED": "true" if redis_enabled else "false",
            "REDIS_BIND_IP": str(redis_bind_ip),
            "REDIS_PORT": str(int(redis_port)),
        },
    )


def _docker_compose_cmd() -> list[str]:
    # Prefer modern plugin syntax.
    p = subprocess.run(["docker", "compose", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if p.returncode == 0:
        return ["docker", "compose"]
    p2 = subprocess.run(["docker-compose", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if p2.returncode == 0:
        return ["docker-compose"]
    raise PipelineError("docker compose not available. Install Docker/Compose first.")


def run_compose(*, hub: Path, args: list[str], capture: bool = False) -> dict[str, Any]:
    compose = _docker_compose_cmd()
    cmd = compose + ["-f", str(hub / "compose" / "docker-compose.yml")] + list(args)
    p = subprocess.run(
        cmd,
        cwd=str(hub / "compose"),
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        check=False,
    )
    return {
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": (p.stdout or "") if capture else "",
        "stderr": (p.stderr or "") if capture else "",
        "cmd": cmd,
    }


def hub_env_path(hub: Path) -> Path:
    return hub / "env" / ".env"


def hub_compose_path(hub: Path) -> Path:
    return hub / "compose" / "docker-compose.yml"


def local_db_dsn(env: dict[str, str]) -> str:
    user = str(env.get("POSTGRES_USER") or "teamos")
    pwd = str(env.get("POSTGRES_PASSWORD") or "")
    db = str(env.get("POSTGRES_DB") or "teamos")
    bind_ip = str(env.get("PG_BIND_IP") or "127.0.0.1")
    port = int(str(env.get("PG_PORT") or "5432"))
    return f"postgresql://{user}:{pwd}@{bind_ip}:{port}/{db}"


def connection_info_md(env: dict[str, str]) -> str:
    pg_user = str(env.get("POSTGRES_USER") or "teamos")
    pg_db = str(env.get("POSTGRES_DB") or "teamos")
    pg_host = str(env.get("PG_BIND_IP") or "127.0.0.1")
    pg_port = str(env.get("PG_PORT") or "5432")
    redis_enabled = str(env.get("HUB_REDIS_ENABLED") or "1") == "1"
    redis_host = str(env.get("REDIS_BIND_IP") or "127.0.0.1")
    redis_port = str(env.get("REDIS_PORT") or "6379")

    lines = [
        "# Team-OS Hub Connection Info",
        "",
        "This file intentionally excludes secrets.",
        "",
        "## Postgres",
        f"- Host: `{pg_host}`",
        f"- Port: `{pg_port}`",
        f"- User: `{pg_user}`",
        f"- DB: `{pg_db}`",
        "- DSN template: `postgresql://<user>:<password>@<host>:<port>/<db>`",
    ]
    if redis_enabled:
        lines.extend(
            [
                "",
                "## Redis",
                f"- Host: `{redis_host}`",
                f"- Port: `{redis_port}`",
                "- URL template: `redis://:<password>@<host>:<port>/0`",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_json_stdout(obj: dict[str, Any]) -> None:
    import json

    print(json.dumps(obj, ensure_ascii=False, indent=2))
