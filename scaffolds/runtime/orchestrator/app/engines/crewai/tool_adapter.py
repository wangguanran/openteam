"""Convert GenericToolDef instances to CrewAI @tool decorated functions."""
from __future__ import annotations

from typing import Any

from app.engines.base import GenericToolDef


def generic_to_crewai_tools(defs: list[GenericToolDef]) -> list[Any]:
    """Wrap each GenericToolDef as a CrewAI tool."""
    from crewai.tools import tool as crewai_tool

    tools: list[Any] = []
    for defn in defs:
        wrapped = crewai_tool(defn.name)(defn.fn)
        wrapped.__doc__ = defn.description
        tools.append(wrapped)
    return tools
