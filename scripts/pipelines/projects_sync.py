#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from typing import Any

from _common import PipelineError


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, timeout_sec: int = 30) -> dict[str, Any]:
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
    except Exception as e:
        raise PipelineError(f"http_failed url={url} err={e}") from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync truth sources to GitHub Projects (wrapper over control plane panel sync)")
    ap.add_argument("--base-url", required=True, help="control plane base url, e.g. http://127.0.0.1:8787")
    ap.add_argument("--project-id", required=True, help="panel project_id (usually teamos or a workspace project id)")
    ap.add_argument("--mode", default="incremental", help="incremental|full")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    base = str(args.base_url).rstrip("/")
    payload = {"project_id": str(args.project_id), "mode": str(args.mode), "dry_run": bool(args.dry_run)}
    out = _http_json("POST", base + "/v1/panel/github/sync", payload, timeout_sec=300)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if bool(out.get("ok", True)) else 2


if __name__ == "__main__":
    raise SystemExit(main())

