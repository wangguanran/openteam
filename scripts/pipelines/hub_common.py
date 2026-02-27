#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

from _common import PipelineError, is_within, read_text, render_template, runtime_hub_root


def hub_root() -> Path:
    return runtime_hub_root()


def hub_env_path(hub: Path) -> Path:
    return hub / "env" / ".env"


def hub_compose_path(hub: Path) -> Path:
    return hub / "compose" / "docker-compose.yml"


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def ensure_dir_secure(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(path, 0o700)


def write_secure_file(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _chmod_best_effort(path, mode)


def enforce_hub_env_config_security(hub: Path) -> None:
    # Enforce strict modes for hub env/config + compose surfaces.
    secure_dirs = [
        hub / "env",
        hub / "config",
        hub / "config" / "postgres",
        hub / "compose",
    ]
    for d in secure_dirs:
        ensure_dir_secure(d)

    secure_files = [
        hub_env_path(hub),
        hub_compose_path(hub),
        hub / "config" / "postgres" / "pg_hba.conf",
        hub / "CONNECTION_INFO.md",
        hub / "README.md",
        hub / "FIREWALL_PLAN.md",
    ]
    for p in secure_files:
        if p.exists():
            _chmod_best_effort(p, 0o600)


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


def render_compose(*, repo_root: Path, pg_bind_ip: str, pg_port: int, redis_bind_ip: str, redis_port: int) -> str:
    tpl = load_template(repo_root, "templates/hub/docker-compose.yml.j2")
    return render_template(
        tpl,
        {
            "PG_BIND_IP": str(pg_bind_ip),
            "PG_PORT": str(int(pg_port)),
            "REDIS_BIND_IP": str(redis_bind_ip),
            "REDIS_PORT": str(int(redis_port)),
        },
    )


def render_pg_hba(*, repo_root: Path, allow_cidrs: list[str]) -> str:
    tpl = load_template(repo_root, "templates/hub/pg_hba.conf.j2")
    rules: list[str] = []
    for cidr in allow_cidrs:
        c = str(cidr or "").strip()
        if not c:
            continue
        rules.append(f"host    all             all             {c:<22} scram-sha-256")
    return render_template(tpl, {"ALLOW_RULES": "\n".join(rules)})


def render_hub_readme(*, repo_root: Path, hub: Path, pg_bind_ip: str, pg_port: int, redis_bind_ip: str, redis_port: int) -> str:
    tpl = load_template(repo_root, "templates/hub/README.md.j2")
    return render_template(
        tpl,
        {
            "HUB_ROOT": str(hub),
            "PG_BIND_IP": str(pg_bind_ip),
            "PG_PORT": str(int(pg_port)),
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


def validate_hub_redis_required(env: dict[str, str], *, env_path: Path) -> None:
    redis_enabled = str(env.get("HUB_REDIS_ENABLED") or "").strip()
    if redis_enabled != "1":
        if not redis_enabled:
            raise PipelineError(f"missing HUB_REDIS_ENABLED=1 in hub env: {env_path}")
        raise PipelineError(f"hub redis is mandatory; HUB_REDIS_ENABLED must be 1 (got: {redis_enabled!r})")

    missing: list[str] = []
    for k in ("REDIS_BIND_IP", "REDIS_PORT", "REDIS_PASSWORD"):
        if not str(env.get(k) or "").strip():
            missing.append(k)
    if missing:
        raise PipelineError(f"missing required redis config in hub env ({', '.join(missing)}): {env_path}")


def validate_hub_postgres_required(env: dict[str, str], *, env_path: Path) -> None:
    missing: list[str] = []
    for k in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "PG_BIND_IP", "PG_PORT"):
        if not str(env.get(k) or "").strip():
            missing.append(k)
    if missing:
        raise PipelineError(f"missing required postgres config in hub env ({', '.join(missing)}): {env_path}")


def load_hub_env_required(hub: Path) -> dict[str, str]:
    env_path = hub_env_path(hub)
    env = parse_env_file(env_path)
    if not env:
        raise PipelineError(f"missing hub env: {env_path} (run: teamos hub init)")
    validate_hub_postgres_required(env, env_path=env_path)
    validate_hub_redis_required(env, env_path=env_path)
    return env


def validate_hub_compose_required(hub: Path) -> None:
    compose = hub_compose_path(hub)
    if not compose.exists():
        raise PipelineError(f"missing compose file: {compose} (run: teamos hub init)")
    text = read_text(compose)
    missing_services: list[str] = []
    if not re.search(r"(?m)^  postgres:\s*$", text):
        missing_services.append("postgres")
    if not re.search(r"(?m)^  redis:\s*$", text):
        missing_services.append("redis")
    if missing_services:
        raise PipelineError(f"hub compose missing required services: {', '.join(missing_services)} ({compose})")


def validate_hub_runtime_path(path: Path, *, hub: Path, label: str) -> None:
    p = path.expanduser().resolve()
    if not is_within(p, hub):
        raise PipelineError(f"{label} must be inside runtime hub root: {hub} (got: {p})")


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
        "",
        "## Redis (Required)",
        f"- Host: `{redis_host}`",
        f"- Port: `{redis_port}`",
        "- URL template: `redis://:<password>@<host>:<port>/0`",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_json_stdout(obj: dict[str, Any]) -> None:
    import json

    print(json.dumps(obj, ensure_ascii=False, indent=2))
