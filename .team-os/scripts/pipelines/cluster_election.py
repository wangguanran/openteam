#!/usr/bin/env python3
"""
Cluster election utilities (deterministic; leader qualification gate).

This pipeline is intentionally focused on *qualification* and policy checks.
Leader lease acquisition/backends are handled by the Control Plane runtime.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, read_yaml, resolve_repo_root


def _allowlist_path(repo_root: Path) -> Path:
    return repo_root / ".team-os" / "policies" / "central_model_allowlist.yaml"


def load_allowlist(repo_root: Path) -> list[str]:
    p = _allowlist_path(repo_root)
    if not p.exists():
        return []
    d = read_yaml(p)
    if not isinstance(d, dict):
        return []
    ids = d.get("allowed_model_ids") or []
    if not isinstance(ids, list):
        return []
    out: list[str] = []
    for x in ids:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return sorted(set(out))


def local_llm_profile() -> dict[str, str]:
    """
    Resolve local llm_profile deterministically from env vars.

    Required for leader qualification in cluster mode:
    - TEAMOS_LLM_MODEL_ID
    Optional:
    - TEAMOS_LLM_PROVIDER (default: codex)
    - TEAMOS_LLM_AUTH_MODE (default: oauth)
    """
    provider = str(os.getenv("TEAMOS_LLM_PROVIDER") or "codex").strip() or "codex"
    model_id = str(os.getenv("TEAMOS_LLM_MODEL_ID") or "").strip()
    auth_mode = str(os.getenv("TEAMOS_LLM_AUTH_MODE") or "oauth").strip() or "oauth"
    return {"provider": provider, "model_id": model_id, "auth_mode": auth_mode}


def qualify_as_leader(*, allowlist: list[str], profile: dict[str, str]) -> dict[str, Any]:
    model_id = str(profile.get("model_id") or "").strip()
    if not model_id:
        return {"qualified": False, "reason": "missing_model_id", "model_id": "", "allowed_model_ids": allowlist}
    if model_id not in set(allowlist):
        return {"qualified": False, "reason": "model_not_allowed", "model_id": model_id, "allowed_model_ids": allowlist}
    return {"qualified": True, "reason": "allowed", "model_id": model_id, "allowed_model_ids": allowlist}


def cmd_qualify(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    allow = load_allowlist(repo)
    profile = local_llm_profile()
    out = qualify_as_leader(allowlist=allow, profile=profile)
    res = {"ok": True, "llm_profile": profile, "qualification": out, "policy_path": str(_allowlist_path(repo))}
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if bool(out.get("qualified")) else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cluster election utilities (qualification gate)")
    add_default_args(ap)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("qualify", help="Check whether this node is allowed to become the Brain").set_defaults(fn=cmd_qualify)
    args = ap.parse_args(argv)
    fn = getattr(args, "fn", None)
    if not fn:
        raise PipelineError("missing subcommand")
    return int(fn(args))


if __name__ == "__main__":
    raise SystemExit(main())

