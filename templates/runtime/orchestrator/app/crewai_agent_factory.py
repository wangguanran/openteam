from __future__ import annotations

from typing import Any, Optional

from . import crewai_role_registry


def build_crewai_agent(
    *,
    role_id: str,
    llm: Any,
    verbose: bool,
    tools_by_profile: Optional[dict[str, list[Any]]] = None,
    template_role_id: str = "",
    goal: str = "",
    backstory: str = "",
    tool_profile: str = "",
    allow_delegation: bool = False,
) -> Any:
    from crewai import Agent

    spec = crewai_role_registry.get_role_spec(role_id, fallback_role_id=template_role_id)
    resolved_goal = str(goal or spec.goal or "").strip()
    resolved_backstory = str(backstory or spec.backstory or "").strip()
    resolved_profile = str(tool_profile or spec.tool_profile or "").strip()
    kwargs: dict[str, Any] = {
        "role": str(role_id or spec.role_id or "").strip(),
        "goal": resolved_goal,
        "backstory": resolved_backstory,
        "llm": llm,
        "allow_delegation": allow_delegation,
        "verbose": verbose,
    }
    if tools_by_profile is not None and resolved_profile:
        kwargs["tools"] = list(tools_by_profile.get(resolved_profile) or [])
    return Agent(**kwargs)

