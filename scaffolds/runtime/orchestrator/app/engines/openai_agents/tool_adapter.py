"""Convert GenericToolDef to OpenAI Agents SDK @function_tool wrapped functions."""
from __future__ import annotations

from typing import Any

from app.engines.base import GenericToolDef


def to_agent_tools(defs: list[GenericToolDef]) -> list[Any]:
    """Wrap each GenericToolDef as an OpenAI Agents SDK function_tool."""
    from agents import function_tool

    tools: list[Any] = []
    for defn in defs:
        wrapped = function_tool(defn.fn)
        wrapped.name = defn.name.replace(" ", "_").lower()
        wrapped.__doc__ = defn.description
        tools.append(wrapped)
    return tools
