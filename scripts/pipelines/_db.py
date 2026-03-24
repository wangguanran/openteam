#!/usr/bin/env python3
"""
Postgres helpers for deterministic pipelines.

Notes:
- We intentionally avoid any ORM and keep dependencies minimal.
- psycopg (v3) is optional but required when OPENTEAM_DB_URL is set and DB-backed features are used.
"""

from __future__ import annotations

import os
from typing import Any

from _common import PipelineError


def get_db_url(*, override: str = "") -> str:
    """
    Resolve the DB DSN.
    Priority: explicit override -> env OPENTEAM_DB_URL.
    """
    dsn = str(override or "").strip()
    if not dsn:
        dsn = str(os.getenv("OPENTEAM_DB_URL") or "").strip()
    return dsn


def connect(dsn: str):
    """
    Connect to Postgres using psycopg (v3).
    Returns a psycopg connection with dict rows.
    """
    dsn = str(dsn or "").strip()
    if not dsn:
        raise PipelineError("missing db dsn (set OPENTEAM_DB_URL or pass --db-url)")
    try:
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
    except Exception as e:  # pragma: no cover
        raise PipelineError('missing dependency: psycopg (install: python3 -m pip install --user "psycopg[binary]")') from e

    # psycopg3 supports connect_timeout kwarg. Keep a short timeout for doctor checks.
    try:
        return psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)
    except TypeError:  # pragma: no cover
        return psycopg.connect(dsn, row_factory=dict_row)


def to_jsonable(v: Any) -> Any:
    """
    Make DB-returned values JSON-serializable.
    psycopg may return datetime objects for TIMESTAMPTZ, etc.
    """
    try:
        import datetime as _dt

        if isinstance(v, (_dt.datetime, _dt.date)):
            return v.isoformat()
    except Exception:
        pass
    return v

