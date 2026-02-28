#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, resolve_repo_root, runtime_state_root, write_json
from _db import connect, get_db_url
from db_migrate import apply_migrations


def _migrations(repo: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for p in sorted((repo / "migrations").glob("*.sql")):
        if len(p.name) >= 4 and p.name[:4].isdigit():
            out.append((p.name[:4], p))
    return out


def _fallback_path(repo: Path) -> Path:
    rt = runtime_state_root(override=str(repo.parent / "team-os-runtime"))
    return rt / "audit" / "installer_knowledge.json"


def _load_fallback(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


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
        rows = cur.execute("SELECT key, value, updated_at FROM installer_knowledge ORDER BY updated_at DESC LIMIT %s", (int(limit),)).fetchall()
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
    ap = argparse.ArgumentParser(description="Installer knowledge store (DB first, runtime fallback)")
    add_default_args(ap)
    ap.add_argument("--db-url", default="")
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
    dsn = get_db_url(override=str(args.db_url or ""))

    out: dict[str, Any] = {"ok": True, "db": {"enabled": bool(dsn)}}

    conn = None
    if dsn:
        try:
            conn = connect(dsn)
            mig = _migrations(repo)
            if mig:
                apply_migrations(conn, mig)
        except Exception as e:
            out["db"] = {"enabled": False, "error": str(e)[:300]}
            conn = None

    fb = _fallback_path(repo)
    fb.parent.mkdir(parents=True, exist_ok=True)

    if args.cmd == "upsert":
        try:
            value_obj = json.loads(str(args.value_json))
        except Exception as e:
            raise PipelineError(f"invalid --value-json: {e}") from e
        if not isinstance(value_obj, dict):
            raise PipelineError("--value-json must be a JSON object")

        if conn is not None:
            _upsert_db(conn, str(args.key), value_obj)
            out["stored"] = {"key": str(args.key), "backend": "db"}
        else:
            data = _load_fallback(fb)
            data[str(args.key)] = value_obj
            write_json(fb, data, dry_run=False)
            out["stored"] = {"key": str(args.key), "backend": "fallback", "path": str(fb)}

    elif args.cmd == "get":
        if conn is not None:
            item = _get_db(conn, str(args.key))
            out["item"] = item
            out["backend"] = "db"
        else:
            data = _load_fallback(fb)
            out["item"] = {"key": str(args.key), "value": (data.get(str(args.key)) or {})}
            out["backend"] = "fallback"
            out["path"] = str(fb)

    else:
        if conn is not None:
            out["items"] = _list_db(conn, int(args.limit))
            out["backend"] = "db"
        else:
            data = _load_fallback(fb)
            items = [{"key": k, "value": v} for k, v in data.items()]
            out["items"] = items[: int(args.limit)]
            out["backend"] = "fallback"
            out["path"] = str(fb)

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
