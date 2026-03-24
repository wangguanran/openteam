from __future__ import annotations

import json
import os
from typing import Any, Optional


_DEFAULT_EVENTS_CHANNEL = "openteam.events"


def _redis_url() -> str:
    return str(os.getenv("OPENTEAM_REDIS_URL") or "").strip()


def _import_redis_module():
    import redis  # type: ignore

    return redis


def _base_status() -> dict[str, Any]:
    return {
        "backend": "redis",
        "configured": False,
        "dependency_ok": False,
        "available": False,
        "reason": "not_configured",
        "error": "",
    }


def _connect(*, probe: bool) -> tuple[Optional[Any], dict[str, Any]]:
    st = _base_status()
    url = _redis_url()
    if not url:
        return None, st

    st["configured"] = True
    try:
        redis = _import_redis_module()
        st["dependency_ok"] = True
    except Exception as e:
        st["reason"] = "dependency_missing"
        st["error"] = str(e)[:200]
        return None, st

    try:
        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        if probe:
            client.ping()
        st["available"] = True
        st["reason"] = "ok"
        return client, st
    except Exception as e:
        st["reason"] = "connection_failed"
        st["error"] = str(e)[:200]
        return None, st


def _event_channel(channel: str) -> str:
    ch = str(channel or "").strip()
    if ch:
        return ch
    env_ch = str(os.getenv("OPENTEAM_REDIS_EVENTS_CHANNEL") or "").strip()
    return env_ch or _DEFAULT_EVENTS_CHANNEL


def _encode_payload(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_payload(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list, int, float, bool)):
        return raw
    s = str(raw)
    try:
        return json.loads(s)
    except Exception:
        return s


def describe() -> dict[str, Any]:
    _client, st = _connect(probe=True)
    return st


def publish_event(channel: str, payload: dict[str, Any]) -> dict[str, Any]:
    ch = _event_channel(channel)
    client, st = _connect(probe=False)
    if client is None:
        return {
            **st,
            "ok": st["reason"] in ("not_configured", "dependency_missing"),
            "skipped": True,
            "channel": ch,
            "published": 0,
        }
    try:
        n = int(client.publish(ch, _encode_payload(payload)))
        return {**st, "ok": True, "skipped": False, "channel": ch, "published": n}
    except Exception as e:
        return {
            **st,
            "ok": False,
            "available": False,
            "reason": "publish_failed",
            "error": str(e)[:200],
            "skipped": False,
            "channel": ch,
            "published": 0,
        }


def enqueue(queue: str, payload: dict[str, Any]) -> dict[str, Any]:
    q = str(queue or "").strip()
    client, st = _connect(probe=False)
    if client is None:
        return {
            **st,
            "ok": st["reason"] in ("not_configured", "dependency_missing"),
            "skipped": True,
            "queue": q,
            "size": 0,
        }
    try:
        size = int(client.rpush(q, _encode_payload(payload)))
        return {**st, "ok": True, "skipped": False, "queue": q, "size": size}
    except Exception as e:
        return {
            **st,
            "ok": False,
            "available": False,
            "reason": "enqueue_failed",
            "error": str(e)[:200],
            "skipped": False,
            "queue": q,
            "size": 0,
        }


def dequeue(queue: str, timeout: int = 0) -> dict[str, Any]:
    q = str(queue or "").strip()
    t = max(0, int(timeout or 0))
    client, st = _connect(probe=False)
    if client is None:
        return {
            **st,
            "ok": st["reason"] in ("not_configured", "dependency_missing"),
            "skipped": True,
            "queue": q,
            "item": None,
            "empty": True,
        }
    try:
        raw = None
        if t > 0:
            row = client.blpop(q, timeout=t)
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                raw = row[1]
        else:
            raw = client.lpop(q)
        item = _decode_payload(raw)
        return {**st, "ok": True, "skipped": False, "queue": q, "item": item, "empty": item is None}
    except Exception as e:
        return {
            **st,
            "ok": False,
            "available": False,
            "reason": "dequeue_failed",
            "error": str(e)[:200],
            "skipped": False,
            "queue": q,
            "item": None,
            "empty": True,
        }


def cache_set(key: str, value: Any, ttl: int = 0) -> dict[str, Any]:
    k = str(key or "").strip()
    t = max(0, int(ttl or 0))
    client, st = _connect(probe=False)
    if client is None:
        return {
            **st,
            "ok": st["reason"] in ("not_configured", "dependency_missing"),
            "skipped": True,
            "key": k,
            "ttl": t,
        }
    try:
        data = _encode_payload(value)
        if t > 0:
            _ = client.setex(k, t, data)
        else:
            _ = client.set(k, data)
        return {**st, "ok": True, "skipped": False, "key": k, "ttl": t}
    except Exception as e:
        return {
            **st,
            "ok": False,
            "available": False,
            "reason": "cache_set_failed",
            "error": str(e)[:200],
            "skipped": False,
            "key": k,
            "ttl": t,
        }


def cache_get(key: str) -> dict[str, Any]:
    k = str(key or "").strip()
    client, st = _connect(probe=False)
    if client is None:
        return {
            **st,
            "ok": st["reason"] in ("not_configured", "dependency_missing"),
            "skipped": True,
            "key": k,
            "hit": False,
            "value": None,
        }
    try:
        raw = client.get(k)
        if raw is None:
            return {**st, "ok": True, "skipped": False, "key": k, "hit": False, "value": None}
        return {**st, "ok": True, "skipped": False, "key": k, "hit": True, "value": _decode_payload(raw)}
    except Exception as e:
        return {
            **st,
            "ok": False,
            "available": False,
            "reason": "cache_get_failed",
            "error": str(e)[:200],
            "skipped": False,
            "key": k,
            "hit": False,
            "value": None,
        }

