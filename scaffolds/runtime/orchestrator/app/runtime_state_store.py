from __future__ import annotations

import os
from typing import Any, Optional

from .runtime_db import RuntimeDB
from .state_store import runtime_state_root


class RuntimeStateStoreError(RuntimeError):
    pass


_DEFAULT_LIMIT = 1000


def _db() -> RuntimeDB:
    db_path = str(os.getenv("RUNTIME_DB_PATH") or "").strip()
    if not db_path:
        db_path = str(runtime_state_root() / "runtime.db")
    return RuntimeDB(db_path)


def get_state(namespace: str, state_key: str, *, default: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    row = _db().get_runtime_state(namespace=str(namespace), state_key=str(state_key))
    if not row:
        return dict(default or {})
    value = row.get("value")
    return dict(value) if isinstance(value, dict) else dict(default or {})


def put_state(namespace: str, state_key: str, value: dict[str, Any]) -> dict[str, Any]:
    payload = dict(value or {})
    _db().put_runtime_state(namespace=str(namespace), state_key=str(state_key), value=payload)
    return payload


def patch_state(namespace: str, state_key: str, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_state(namespace, state_key)
    current.update(dict(patch or {}))
    put_state(namespace, state_key, current)
    return current


def delete_state(namespace: str, state_key: str) -> None:
    _db().delete_runtime_state(namespace=str(namespace), state_key=str(state_key))



def list_state(namespace: str, *, prefix: str = "") -> list[dict[str, Any]]:
    return list(_db().list_runtime_state(namespace=str(namespace), prefix=str(prefix or "")) or [])



def get_doc(namespace: str, doc_id: str) -> Optional[dict[str, Any]]:
    row = _db().get_runtime_doc(namespace=str(namespace), doc_id=str(doc_id))
    if not row:
        return None
    value = row.get("value")
    return dict(value) if isinstance(value, dict) else None



def put_doc(
    namespace: str,
    doc_id: str,
    *,
    project_id: str = "",
    scope_id: str = "",
    state: str = "",
    category: str = "",
    value: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(value or {})
    _db().put_runtime_doc(
        namespace=str(namespace),
        doc_id=str(doc_id),
        project_id=str(project_id or payload.get("project_id") or ""),
        scope_id=str(scope_id or payload.get("target_id") or payload.get("scope_id") or ""),
        state=str(state or payload.get("status") or ""),
        category=str(category or payload.get("lane") or payload.get("category") or ""),
        value=payload,
    )
    return payload



def delete_doc(namespace: str, doc_id: str) -> None:
    _db().delete_runtime_doc(namespace=str(namespace), doc_id=str(doc_id))



def list_docs(
    namespace: str,
    *,
    project_id: str = "",
    scope_id: str = "",
    state: str = "",
    category: str = "",
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    rows = _db().list_runtime_docs(
        namespace=str(namespace),
        project_id=str(project_id or "") or None,
        scope_id=str(scope_id or "") or None,
        state=str(state or "") or None,
        category=str(category or "") or None,
        limit=max(1, int(limit or _DEFAULT_LIMIT)),
    )
    out: list[dict[str, Any]] = []
    for row in rows or []:
        value = row.get("value")
        if isinstance(value, dict):
            out.append(dict(value))
    return out
