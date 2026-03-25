"""Convert GenericToolDef to Anthropic tool_use JSON schema format."""
from __future__ import annotations

import inspect
from typing import Any

from app.engines.base import GenericToolDef


def _infer_input_schema(defn: GenericToolDef) -> dict[str, Any]:
    """Infer JSON Schema from function signature or explicit parameters."""
    if defn.parameters:
        return dict(defn.parameters)
    sig = inspect.signature(defn.fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        properties[name] = {"type": "string", "description": name}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def to_anthropic_tools(defs: list[GenericToolDef]) -> list[dict[str, Any]]:
    """Convert GenericToolDef list to Anthropic tool definitions."""
    tools: list[dict[str, Any]] = []
    for defn in defs:
        tools.append({
            "name": defn.name.replace(" ", "_").lower(),
            "description": defn.description,
            "input_schema": _infer_input_schema(defn),
        })
    return tools


def tool_name_to_fn(defs: list[GenericToolDef]) -> dict[str, GenericToolDef]:
    """Build a lookup from Anthropic tool name -> GenericToolDef."""
    return {
        defn.name.replace(" ", "_").lower(): defn
        for defn in defs
    }
