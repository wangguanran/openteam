"""Engine-agnostic LLM configuration builder.

Extracts the common config resolution logic from llm_factory so all
engines share the same environment-variable and workflow-override semantics.
"""
from __future__ import annotations

import os
from typing import Any

from app.engines.base import EngineLLMConfig


def build_llm_config(*, workflow: Any = None) -> EngineLLMConfig:
    model = str(os.getenv("OPENTEAM_LLM_MODEL") or "openai/gpt-5.4").strip()
    base_url = str(os.getenv("OPENTEAM_LLM_BASE_URL") or "").strip()
    api_key = str(os.getenv("OPENTEAM_LLM_API_KEY") or "").strip()

    if workflow is not None:
        override_base_url = str(getattr(workflow, "llm_url", "") or "").strip()
        override_api_key = str(getattr(workflow, "llm_api_key", "") or "").strip()
        if override_base_url:
            base_url = override_base_url
        if override_api_key:
            api_key = override_api_key

    reasoning_effort = str(
        os.getenv("OPENTEAM_CREWAI_REASONING_EFFORT") or "xhigh"
    ).strip().lower()
    aliases = {"highest": "xhigh", "max": "xhigh"}
    reasoning_effort = aliases.get(reasoning_effort, reasoning_effort)
    if reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        reasoning_effort = "xhigh"

    max_retries = 3
    max_retries_raw = str(os.getenv("OPENTEAM_CREWAI_MAX_RETRIES") or "").strip()
    if max_retries_raw:
        try:
            max_retries = max(0, int(max_retries_raw))
        except Exception:
            pass

    extra: dict[str, Any] = {}
    auth_mode = str(os.getenv("OPENTEAM_CREWAI_AUTH_MODE") or "").strip().lower()
    if auth_mode:
        extra["auth_mode"] = auth_mode

    return EngineLLMConfig(
        model=model,
        base_url=base_url,
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        max_tokens=4096,
        max_retries=max_retries,
        extra=extra,
    )
