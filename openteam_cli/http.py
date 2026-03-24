"""HTTP client utilities and SSE event parsing for the OpenTeam CLI."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


def _http_json(
    method: str,
    url: str,
    payload: Optional[dict[str, Any]] = None,
    timeout_sec: int = 10,
    *,
    _redirect_depth: int = 0,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        # Leader-only writes: auto-forward to Brain when server returns 409 with leader info.
        if e.code == 409 and _redirect_depth < 1:
            try:
                j = json.loads(body) if body else {}
            except Exception:
                j = {}
            leader_base = ""
            if isinstance(j, dict):
                leader_base = str(j.get("leader_base_url") or "").strip()
                if not leader_base and isinstance(j.get("detail"), dict):
                    leader_base = str((j.get("detail") or {}).get("leader_base_url") or "").strip()
            if leader_base:
                p = urllib.parse.urlparse(url)
                leader_base = leader_base.rstrip("/")
                new_url = leader_base + p.path
                if p.query:
                    new_url += "?" + p.query
                return _http_json(method, new_url, payload, timeout_sec, _redirect_depth=_redirect_depth + 1)
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body[:2000]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP request failed: {e}") from e


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _iter_sse_events(resp: Any):
    event_type = "message"
    data_lines: list[str] = []
    event_id = ""
    while True:
        raw = resp.readline()
        if not raw:
            if data_lines:
                payload_text = "\n".join(data_lines)
                yield {
                    "event": event_type,
                    "id": event_id,
                    "data": _safe_json_loads(payload_text) if payload_text else {},
                }
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                payload_text = "\n".join(data_lines)
                yield {
                    "event": event_type,
                    "id": event_id,
                    "data": _safe_json_loads(payload_text) if payload_text else {},
                }
            event_type = "message"
            data_lines = []
            event_id = ""
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip() or "message"
            continue
        if line.startswith("id:"):
            event_id = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
