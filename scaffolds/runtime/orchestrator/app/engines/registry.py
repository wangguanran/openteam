"""Engine registry: lazy-loads and looks up execution engines by ID."""
from __future__ import annotations

import os
from typing import Any

from app.engines.base import ExecutionEngine

_ENGINES: dict[str, ExecutionEngine] = {}
_DEFAULT_ENGINE_ID = "crewai"
_REGISTERED = False


def register_engine(engine: ExecutionEngine) -> None:
    _ENGINES[engine.engine_id] = engine


def _auto_register() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    if "crewai" not in _ENGINES:
        try:
            from app.engines.crewai.engine import CrewAIEngine
            register_engine(CrewAIEngine())
        except Exception:
            pass
    if "claude" not in _ENGINES:
        try:
            from app.engines.claude.engine import ClaudeEngine
            register_engine(ClaudeEngine())
        except Exception:
            pass
    if "openai_agents" not in _ENGINES:
        try:
            from app.engines.openai_agents.engine import OpenAIAgentsEngine
            register_engine(OpenAIAgentsEngine())
        except Exception:
            pass


def get_engine(engine_id: str = "") -> ExecutionEngine:
    _auto_register()
    resolved = (
        str(engine_id or "").strip().lower()
        or str(os.getenv("OPENTEAM_ENGINE") or "").strip().lower()
        or _DEFAULT_ENGINE_ID
    )
    engine = _ENGINES.get(resolved)
    if engine is None:
        raise KeyError(
            f"unknown execution engine: {resolved}; registered={list(_ENGINES.keys())}"
        )
    return engine


def list_engines() -> list[str]:
    _auto_register()
    return sorted(_ENGINES.keys())
