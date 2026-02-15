import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional, Tuple


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit_n8n_event(
    event_type: str,
    *,
    project_id: str,
    workstream_id: str,
    payload: Optional[dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Optional notification hook (n8n). n8n is NOT the source of truth or the primary panel.

    - Disabled unless env N8N_WEBHOOK_URL is set.
    - Best-effort: failures must not break control-plane behavior.
    - Never sends secrets (do not include tokens/keys in payload).
    """
    url = (os.getenv("N8N_WEBHOOK_URL") or "").strip()
    if not url:
        return False, "disabled"

    body = {
        "ts": _utc_now_iso(),
        "event_type": str(event_type or "").strip(),
        "project_id": str(project_id or "").strip(),
        "workstream_id": str(workstream_id or "").strip(),
        "payload": payload or {},
    }

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, method="POST", data=data, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            _ = resp.read()
        return True, "ok"
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        return False, f"http_{e.code}:{msg[:200]}"
    except Exception as e:
        return False, str(e)[:200]

