#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from _common import PipelineError, add_default_args, append_jsonl, resolve_repo_root, runtime_root, utc_now_iso
from _db import connect, get_db_url
from db_migrate import apply_migrations as _apply_migrations


def _emit_json(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _parse_bool(raw: Any, *, field: str) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw or "").strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    raise PipelineError(f"invalid {field}: {raw!r} (expected true/false)")


def _clip_text(text: str, *, max_chars: int = 2000) -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def _redact_secrets(text: str) -> str:
    s = str(text or "")
    # URL secrets.
    s = re.sub(r"(postgres(?:ql)?://[^:\s/]+:)([^@/\s]+)(@)", r"\1***\3", s, flags=re.IGNORECASE)
    s = re.sub(r"(redis://:)([^@/\s]+)(@)", r"\1***\3", s, flags=re.IGNORECASE)
    # Common key/value secret patterns.
    s = re.sub(r"((?:password|passwd|token|secret|api[_-]?key)\s*[=:]\s*)([^\s]+)", r"\1***", s, flags=re.IGNORECASE)
    return s


def _safe_tail(text: str, *, max_chars: int = 2000) -> str:
    return _redact_secrets(_clip_text(text, max_chars=max_chars))


def classify_failure(*, component: str, stage: str, stdout: str, stderr: str, ok: bool) -> dict[str, Any]:
    if ok:
        return {"category": "SUCCESS", "retryable": False, "remediation": "none"}

    comp = str(component or "").strip().lower()
    stg = str(stage or "").strip().lower()
    blob = "\n".join([comp, stg, str(stdout or ""), str(stderr or "")]).lower()

    if (
        "missing required args" in blob
        or "unknown arg" in blob
        or "--password-stdin was provided but stdin was empty" in blob
        or "no password was provided on stdin" in blob
    ):
        return {"category": "INPUT_ERROR", "retryable": False, "remediation": "fix CLI args/input and retry"}

    if "sshpass is required" in blob or "sshpass: not found" in blob or "command not found: sshpass" in blob:
        return {"category": "DEPENDENCY_MISSING", "retryable": False, "remediation": "install sshpass or use ssh key auth"}

    if "host key verification failed" in blob:
        return {"category": "SSH_HOST_KEY", "retryable": False, "remediation": "update known_hosts for the target host"}

    if (
        "permission denied (publickey" in blob
        or "permission denied (password" in blob
        or "permission denied, please try again" in blob
        or "authentication failed" in blob
    ):
        return {"category": "SSH_AUTH_FAILED", "retryable": False, "remediation": "verify remote user, ssh key/password, and sudo rights"}

    if (
        "connection timed out" in blob
        or "no route to host" in blob
        or "connection refused" in blob
        or "could not resolve hostname" in blob
        or "name or service not known" in blob
        or "network is unreachable" in blob
    ):
        return {"category": "NETWORK_UNREACHABLE", "retryable": True, "remediation": "check network/DNS/firewall and retry"}

    if "permission denied" in blob and ("install -m" in blob or "mkdir -p" in blob or stg.startswith("ssh")):
        return {"category": "REMOTE_PERMISSION", "retryable": False, "remediation": "verify write permission on remote ~/.teamos paths"}

    if "missing hub env" in blob or "missing required postgres config" in blob or "missing required redis config" in blob:
        return {"category": "BRAIN_CONFIG_MISSING", "retryable": False, "remediation": "run `teamos hub init` / validate central hub env, then retry"}

    if stg.startswith("scp"):
        return {"category": "SCP_FAILED", "retryable": True, "remediation": "check ssh/scp connectivity and remote reachability, then retry"}
    if stg.startswith("ssh"):
        return {"category": "SSH_REMOTE_COMMAND_FAILED", "retryable": False, "remediation": "inspect remote command stderr and fix remote state"}

    return {"category": "UNKNOWN", "retryable": False, "remediation": "inspect installer stderr/stdout and classify manually"}


def _ensure_db_schema(conn, *, repo_root: Path) -> None:
    mig_dir = repo_root / "tooling" / "migrations"
    migrations: list[tuple[str, Path]] = []
    for p in sorted(mig_dir.glob("*.sql")):
        name = p.name
        if len(name) >= 4 and name[:4].isdigit():
            migrations.append((name[:4], p))
    if migrations:
        _apply_migrations(conn, migrations)


def _db_insert_run(conn, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO installer_runs (
              run_id, ts, instance_id, target_host, ok, category, detail
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s::jsonb
            )
            """,
            (
                row["run_id"],
                row["ts"],
                row.get("instance_id", ""),
                row.get("target_host", ""),
                bool(row.get("ok")),
                row.get("category", ""),
                json.dumps(row.get("detail") or {}, ensure_ascii=False),
            ),
        )
    conn.commit()


def _db_upsert_knowledge(conn, *, key: str, value: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO installer_knowledge (key, value, updated_at)
            VALUES (%s, %s::jsonb, now())
            ON CONFLICT (key)
            DO UPDATE SET value=EXCLUDED.value, updated_at=now()
            """,
            (str(key), json.dumps(value, ensure_ascii=False)),
        )
    conn.commit()


def _fallback_path(*, runtime_root_override: str) -> Path:
    rt = runtime_root(override=runtime_root_override)
    return rt / "state" / "audit" / "installer_runs.jsonl"


def _write_fallback(*, runtime_root_override: str, event: dict[str, Any]) -> Path:
    p = _fallback_path(runtime_root_override=runtime_root_override)
    append_jsonl(p, event, dry_run=False)
    return p


def _knowledge_key(component: str, category: str) -> str:
    comp = re.sub(r"[^a-z0-9_.-]+", "_", str(component or "").lower()).strip("_") or "unknown"
    cat = re.sub(r"[^a-z0-9_.-]+", "_", str(category or "").lower()).strip("_") or "unknown"
    return f"installer:{comp}:{cat}"


def _load_input(args: argparse.Namespace) -> dict[str, Any]:
    if str(getattr(args, "input_json", "") or "").strip():
        raw = str(args.input_json).strip()
        if raw == "-":
            raw = sys.stdin.read()
        try:
            obj = json.loads(raw)
        except Exception as e:
            raise PipelineError(f"invalid --input-json: {e}") from e
        if not isinstance(obj, dict):
            raise PipelineError("--input-json must be a JSON object")
        return obj
    return {
        "component": getattr(args, "component", ""),
        "stage": getattr(args, "stage", ""),
        "stdout": getattr(args, "stdout", ""),
        "stderr": getattr(args, "stderr", ""),
        "target_host": getattr(args, "target_host", ""),
        "ok": getattr(args, "ok", ""),
    }


def _normalize_input(raw: dict[str, Any]) -> dict[str, Any]:
    component = str(raw.get("component") or "").strip()
    if not component:
        raise PipelineError("component is required")
    ok_raw = raw.get("ok", None)
    if ok_raw is None or str(ok_raw).strip() == "":
        raise PipelineError("ok is required")
    ok = _parse_bool(ok_raw, field="ok")
    return {
        "component": component,
        "stage": str(raw.get("stage") or "").strip(),
        "stdout": str(raw.get("stdout") or ""),
        "stderr": str(raw.get("stderr") or ""),
        "target_host": str(raw.get("target_host") or "").strip(),
        "ok": ok,
    }


def cmd_classify(args: argparse.Namespace) -> int:
    data = _normalize_input(_load_input(args))
    cls = classify_failure(
        component=data["component"],
        stage=data["stage"],
        stdout=data["stdout"],
        stderr=data["stderr"],
        ok=bool(data["ok"]),
    )
    out = {
        "ok": True,
        "input": {
            "component": data["component"],
            "stage": data["stage"],
            "target_host": data["target_host"],
            "ok": bool(data["ok"]),
        },
        "classification": cls,
    }
    _emit_json(out)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    data = _normalize_input(_load_input(args))

    cls = classify_failure(
        component=data["component"],
        stage=data["stage"],
        stdout=data["stdout"],
        stderr=data["stderr"],
        ok=bool(data["ok"]),
    )
    now = utc_now_iso()
    detail = {
        "component": data["component"],
        "stage": data["stage"],
        "retryable": bool(cls["retryable"]),
        "remediation": str(cls["remediation"]),
        "stdout_tail": _safe_tail(data["stdout"]),
        "stderr_tail": _safe_tail(data["stderr"]),
    }
    run = {
        "run_id": str(uuid.uuid4()),
        "ts": now,
        "instance_id": str(getattr(args, "instance_id", "") or "").strip() or str(socket.gethostname() or ""),
        "target_host": data["target_host"],
        "ok": bool(data["ok"]),
        "category": str(cls["category"]),
        "detail": detail,
    }
    knowledge_key = _knowledge_key(data["component"], str(cls["category"]))
    knowledge_value = {
        "component": data["component"],
        "category": str(cls["category"]),
        "retryable": bool(cls["retryable"]),
        "remediation": str(cls["remediation"]),
        "last_stage": data["stage"],
        "last_target_host": data["target_host"],
        "last_ok": bool(data["ok"]),
        "updated_at": now,
    }

    dsn = get_db_url(override=str(getattr(args, "db_url", "") or ""))
    db_enabled = bool(str(dsn or "").strip())
    db_error: Optional[str] = None
    fallback_path: Optional[Path] = None

    if db_enabled:
        conn = None
        try:
            conn = connect(dsn)
            _ensure_db_schema(conn, repo_root=repo)
            _db_insert_run(conn, run)
            _db_upsert_knowledge(conn, key=knowledge_key, value=knowledge_value)
        except Exception as e:
            db_error = str(e)
            db_enabled = False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if not db_enabled:
        event = {
            "ts": now,
            "event": "INSTALLER_RUN",
            "pending_sync": True,
            "run": run,
            "classification": cls,
            "knowledge": {"key": knowledge_key, "value": knowledge_value},
            "db_error": db_error or "",
        }
        fallback_path = _write_fallback(runtime_root_override=str(getattr(args, "runtime_root", "") or ""), event=event)

    out: dict[str, Any] = {
        "ok": True,
        "run": {k: v for k, v in run.items() if k != "detail"},
        "classification": cls,
        "db": {"enabled": db_enabled},
    }
    if fallback_path is not None:
        out["fallback_path"] = str(fallback_path)
    if db_error:
        out["db_error"] = db_error
    _emit_json(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic installer run classifier/recorder")
    add_default_args(ap)
    ap.add_argument("--db-url", default="", help="override TEAMOS_DB_URL")
    ap.add_argument("--runtime-root", default="", help="override runtime root (fallback audit path)")
    ap.add_argument("--instance-id", default="", help="override installer instance id")
    ap.add_argument("--input-json", default="", help="JSON object for classifier input; '-' means stdin")

    sp = ap.add_subparsers(dest="subcmd", required=True)

    def _add_common_args(c: argparse.ArgumentParser) -> None:
        c.add_argument("--component", default="")
        c.add_argument("--stage", default="")
        c.add_argument("--stdout", default="")
        c.add_argument("--stderr", default="")
        c.add_argument("--target-host", default="")
        c.add_argument("--ok", default="")

    c = sp.add_parser("classify", help="Classify installer output")
    _add_common_args(c)
    c.set_defaults(fn=cmd_classify)

    r = sp.add_parser("record", help="Classify and persist installer run")
    _add_common_args(r)
    r.set_defaults(fn=cmd_record)

    args = ap.parse_args(argv)
    try:
        return int(args.fn(args))
    except PipelineError as e:
        _emit_json({"ok": False, "error": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
