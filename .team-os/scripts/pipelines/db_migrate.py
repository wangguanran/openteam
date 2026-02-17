#!/usr/bin/env python3
"""
Postgres migrations runner (deterministic).

Usage:
  TEAMOS_DB_URL=postgresql://... ./teamos db migrate

Design:
- Apply .team-os/db/migrations/*.sql in lexicographic order.
- Track applied versions in schema_migrations(version).
- Idempotent by design; migrations should use IF NOT EXISTS / safe ALTERs.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

from _common import PipelineError, add_default_args, read_text, resolve_repo_root
from _db import connect, get_db_url


_MIG_RE = re.compile(r"^(?P<ver>[0-9]{4})_.*\.sql$")


def split_sql_statements(sql_text: str) -> list[str]:
    """
    Split SQL into individual statements by semicolons, ignoring semicolons inside:
    - single quotes: '...'
    - double quotes: "..."
    - dollar-quoted strings: $tag$...$tag$
    - line comments: -- ... \\n
    - block comments: /* ... */
    """
    s = str(sql_text or "")
    out: list[str] = []
    buf: list[str] = []

    in_sq = False
    in_dq = False
    in_dollar = False
    dollar_tag = ""

    i = 0
    n = len(s)

    def flush() -> None:
        stmt = "".join(buf).strip()
        buf.clear()
        if stmt:
            out.append(stmt)

    while i < n:
        ch = s[i]
        nxt = s[i + 1] if (i + 1) < n else ""

        if not in_sq and not in_dq and not in_dollar:
            # line comment
            if ch == "-" and nxt == "-":
                i += 2
                while i < n and s[i] not in "\r\n":
                    i += 1
                continue
            # block comment
            if ch == "/" and nxt == "*":
                i += 2
                while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                    i += 1
                i += 2 if i + 1 < n else 0
                continue

            # dollar-quote start
            if ch == "$":
                m = re.match(r"\$[A-Za-z0-9_]*\$", s[i:])
                if m:
                    in_dollar = True
                    dollar_tag = m.group(0)
                    buf.append(dollar_tag)
                    i += len(dollar_tag)
                    continue

            if ch == "'":
                in_sq = True
                buf.append(ch)
                i += 1
                continue
            if ch == '"':
                in_dq = True
                buf.append(ch)
                i += 1
                continue
            if ch == ";":
                flush()
                i += 1
                continue

            buf.append(ch)
            i += 1
            continue

        if in_sq:
            buf.append(ch)
            # escaped quote ''
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_sq = False
            i += 1
            continue

        if in_dq:
            buf.append(ch)
            # escaped identifier quote ""
            if ch == '"' and nxt == '"':
                buf.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_dq = False
            i += 1
            continue

        if in_dollar:
            if dollar_tag and s.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                in_dollar = False
                dollar_tag = ""
                continue
            buf.append(ch)
            i += 1
            continue

    flush()
    return out


def _list_migrations(migrations_dir: Path) -> list[tuple[str, Path]]:
    if not migrations_dir.exists():
        return []
    items: list[tuple[str, Path]] = []
    for p in sorted(migrations_dir.glob("*.sql")):
        m = _MIG_RE.match(p.name)
        if not m:
            continue
        items.append((m.group("ver"), p))
    return items


def _ensure_schema_migrations(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def _applied_versions(conn) -> set[str]:
    _ensure_schema_migrations(conn)
    with conn.cursor() as cur:
        try:
            rows = cur.execute("SELECT version FROM schema_migrations ORDER BY version ASC").fetchall()
        except Exception:
            return set()
    out: set[str] = set()
    for r in rows or []:
        try:
            out.add(str(r.get("version") or "").strip())
        except Exception:
            pass
    return {v for v in out if v}


def apply_migrations(conn, migrations: Iterable[tuple[str, Path]]) -> dict[str, Any]:
    """
    Apply migrations to an existing connection.
    Returns structured result.
    """
    _ensure_schema_migrations(conn)
    applied = _applied_versions(conn)

    applied_now: list[str] = []
    skipped: list[str] = []

    for ver, path in list(migrations):
        if ver in applied:
            skipped.append(ver)
            continue
        sql_text = read_text(path)
        statements = split_sql_statements(sql_text)
        try:
            with conn.cursor() as cur:
                for stmt in statements:
                    s = stmt.strip()
                    if not s:
                        continue
                    cur.execute(s)
                # Mark migration applied (idempotent).
                cur.execute("INSERT INTO schema_migrations(version) VALUES (%s) ON CONFLICT(version) DO NOTHING", (ver,))
            conn.commit()
            applied_now.append(ver)
        except Exception as e:
            conn.rollback()
            raise PipelineError(f"migration_failed version={ver} file={path.name} error={str(e)[:200]}") from e

    return {"ok": True, "applied": applied_now, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply Team OS Postgres migrations (deterministic)")
    add_default_args(ap)
    ap.add_argument("--db-url", default="", help="override TEAMOS_DB_URL")
    ap.add_argument("--migrations-dir", default="", help="override migrations dir (default: <repo>/.team-os/db/migrations)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="plan only; do not execute statements")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    mig_dir = Path(str(args.migrations_dir or "")).expanduser().resolve() if str(args.migrations_dir or "").strip() else (repo / ".team-os" / "db" / "migrations")
    migrations = _list_migrations(mig_dir)
    if not migrations:
        raise PipelineError(f"no migrations found: {mig_dir}")

    if bool(args.dry_run):
        out = {"ok": True, "dry_run": True, "migrations": [{"version": v, "file": p.name} for (v, p) in migrations]}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    dsn = get_db_url(override=str(args.db_url or ""))
    if not dsn:
        raise PipelineError("missing TEAMOS_DB_URL (or pass --db-url)")

    conn = connect(dsn)
    try:
        out = apply_migrations(conn, migrations)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if bool(out.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
