from __future__ import annotations

import os
from typing import Any

from app import codex_llm
from app import crewai_runtime


def _ensure_codex_proxy_bypass() -> None:
    bypass_hosts = [
        "chatgpt.com",
        ".chatgpt.com",
        "api.openai.com",
        ".openai.com",
    ]
    current = str(os.getenv("NO_PROXY") or os.getenv("no_proxy") or "").strip()
    entries: list[str] = []
    seen: set[str] = set()
    for raw in current.split(","):
        item = str(raw or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        entries.append(item)
    for item in bypass_hosts:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        entries.append(item)
    if not entries:
        return
    resolved = ",".join(entries)
    os.environ["NO_PROXY"] = resolved
    os.environ["no_proxy"] = resolved


def build_crewai_llm(*, workflow: Any | None = None):
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    crewai_runtime.require_crewai_importable(refresh=True)
    from crewai.llm import LLM

    model = str(os.getenv("TEAMOS_LLM_MODEL") or "openai/gpt-5.4").strip()
    base_url = str(os.getenv("TEAMOS_LLM_BASE_URL") or "").strip()
    api_key = str(os.getenv("TEAMOS_LLM_API_KEY") or "").strip()
    if workflow is not None:
        override_base_url = str(getattr(workflow, "llm_url", "") or "").strip()
        override_api_key = str(getattr(workflow, "llm_api_key", "") or "").strip()
        if override_base_url:
            base_url = override_base_url
        if override_api_key:
            api_key = override_api_key
    auth_mode = str(os.getenv("TEAMOS_CREWAI_AUTH_MODE") or "").strip().lower()

    logged_in = False
    if "codex" in model.lower():
        try:
            logged_in, _ = codex_llm.codex_login_status()
        except codex_llm.CodexUnavailable:
            logged_in = False

    explicit_llm_credentials = bool(api_key) or bool(base_url)
    if logged_in and "codex" in model.lower() and not explicit_llm_credentials:
        os.environ["TEAMOS_CREWAI_AUTH_MODE"] = "oauth_codex"
        os.environ.pop("OPENAI_OAUTH_ACCESS_TOKEN", None)
        os.environ.pop("OPENAI_ACCESS_TOKEN", None)
        api_key = ""
        base_url = ""
    elif (not auth_mode) and ("codex" in model.lower()) and (not api_key):
        os.environ["TEAMOS_CREWAI_AUTH_MODE"] = "oauth_codex"

    if "codex" in model.lower() and str(os.getenv("TEAMOS_CREWAI_AUTH_MODE") or "").strip().lower() == "oauth_codex":
        _ensure_codex_proxy_bypass()

    reasoning_effort = str(os.getenv("TEAMOS_CREWAI_REASONING_EFFORT") or "xhigh").strip().lower()
    reasoning_effort_aliases = {
        "highest": "xhigh",
        "max": "xhigh",
    }
    reasoning_effort = reasoning_effort_aliases.get(reasoning_effort, reasoning_effort)
    if reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        reasoning_effort = "xhigh"

    kwargs: dict[str, Any] = {
        "model": model,
        "api": "responses",
        "is_litellm": "openrouter" in model.lower(),
        "max_tokens": 4000,
    }
    max_retries_raw = str(os.getenv("TEAMOS_CREWAI_MAX_RETRIES") or "").strip()
    if max_retries_raw:
        try:
            kwargs["max_retries"] = max(0, int(max_retries_raw))
        except Exception:
            pass
    if any(token in model.lower() for token in ("gpt-5", "codex", "o1", "o3", "o4")):
        kwargs["reasoning_effort"] = reasoning_effort
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    return LLM(**kwargs)
