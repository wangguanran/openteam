#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, resolve_repo_root, runtime_state_root, write_json


def _fallback_path(*, runtime_root_override: str = ""):
    rt = runtime_state_root(override=runtime_root_override)
    return rt / "audit" / "installer_knowledge.json"


def _load_fallback(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


@contextlib.contextmanager
def _exclusive_fallback_lock(path: Path):
    lock_path = _lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)


def upsert_fallback(*, runtime_root_override: str, key: str, value: dict[str, Any], dry_run: bool = False):
    path = _fallback_path(runtime_root_override=runtime_root_override)
    with _exclusive_fallback_lock(path):
        data = _load_fallback(path)
        data[str(key)] = value
        write_json(path, data, dry_run=dry_run)
    return path


def _upsert_db(conn: Any, key: str, value: dict[str, Any]) -> None:
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


def _get_db(conn: Any, key: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        row = cur.execute("SELECT key, value, updated_at FROM installer_knowledge WHERE key=%s", (str(key),)).fetchone()
    if not row:
        return {}
    d = dict(row)
    return {
        "key": str(d.get("key") or ""),
        "value": d.get("value") if isinstance(d.get("value"), dict) else {},
        "updated_at": str(d.get("updated_at") or ""),
    }


def _list_db(conn: Any, limit: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        rows = cur.execute(
            "SELECT key, value, updated_at FROM installer_knowledge ORDER BY updated_at DESC LIMIT %s",
            (int(limit),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows or []:
        d = dict(r)
        out.append(
            {
                "key": str(d.get("key") or ""),
                "value": d.get("value") if isinstance(d.get("value"), dict) else {},
                "updated_at": str(d.get("updated_at") or ""),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Installer knowledge store (single-node runtime fallback)")
    add_default_args(ap)
    ap.add_argument("--db-url", default="", help="deprecated in single-node mode; ignored")
    ap.add_argument("--runtime-root", default="", help="override runtime root for fallback storage")
    ap.add_argument("--json", action="store_true")
    sp = ap.add_subparsers(dest="cmd", required=True)

    up = sp.add_parser("upsert")
    up.add_argument("key")
    up.add_argument("--value-json", required=True)

    get = sp.add_parser("get")
    get.add_argument("key")

    ls = sp.add_parser("list")
    ls.add_argument("--limit", type=int, default=50)

    args = ap.parse_args(argv)
    repo = resolve_repo_root(args)
    _ = repo

    out: dict[str, Any] = {"ok": True, "db": {"enabled": False}}

    fb = _fallback_path(runtime_root_override=str(getattr(args, "runtime_root", "") or ""))
    fb.parent.mkdir(parents=True, exist_ok=True)

    if args.cmd == "upsert":
        try:
            value_obj = json.loads(str(args.value_json))
        except Exception as e:
            raise PipelineError(f"invalid --value-json: {e}") from e
        if not isinstance(value_obj, dict):
            raise PipelineError("--value-json must be a JSON object")

        fb = upsert_fallback(
            runtime_root_override=str(getattr(args, "runtime_root", "") or ""),
            key=str(args.key),
            value=value_obj,
            dry_run=False,
        )
        out["stored"] = {"key": str(args.key), "backend": "fallback", "path": str(fb)}

    elif args.cmd == "get":
        data = _load_fallback(fb)
        out["item"] = {"key": str(args.key), "value": (data.get(str(args.key)) or {})}
        out["backend"] = "fallback"
        out["path"] = str(fb)

    else:
        data = _load_fallback(fb)
        items = [{"key": k, "value": v} for k, v in data.items()]
        out["items"] = items[: int(args.limit)]
        out["backend"] = "fallback"
        out["path"] = str(fb)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
