from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class GitHubChecksError(Exception):
    pass


def checks_writes_enabled() -> bool:
    return str(os.getenv("OPENTEAM_GH_CHECKS_WRITE_ENABLED") or "0").strip().lower() in {"1", "true", "yes", "on"}


def create_check_run(*, repo_full_name: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo_full_name}/check-runs",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))
