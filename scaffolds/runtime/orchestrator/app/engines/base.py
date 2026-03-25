"""Engine-agnostic abstractions for multi-backend agent execution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from app.pydantic_compat import BaseModel


@dataclass(frozen=True)
class EngineLLMConfig:
    """Engine-agnostic LLM configuration."""

    model: str = ""
    base_url: str = ""
    api_key: str = ""
    reasoning_effort: str = "xhigh"
    max_tokens: int = 4096
    max_retries: int = 3
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineAgentSpec:
    """Engine-agnostic agent specification."""

    role_id: str
    goal: str = ""
    backstory: str = ""
    tool_profile: str = ""
    allow_delegation: bool = False


@dataclass(frozen=True)
class EngineTaskSpec:
    """Engine-agnostic task specification."""

    name: str
    description: str
    expected_output: str
    agent_spec: EngineAgentSpec
    output_model: type[BaseModel] | None = None


@dataclass
class EngineTaskResult:
    """Engine-agnostic task result."""

    ok: bool = True
    raw: str = ""
    parsed: dict[str, Any] | None = None
    agent_id: str = ""
    role_id: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_workflow_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "kind": "engine_task",
            "agent_id": self.agent_id,
            "role_id": self.role_id,
            "raw": self.raw,
            "outputs": {"raw": self.raw},
        }
        if self.parsed is not None:
            payload["outputs"]["json"] = self.parsed
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class GenericToolDef:
    """Engine-agnostic tool definition.

    Each engine adapter converts these into native tool objects
    (CrewAI @tool, Anthropic tool_use schema, OpenAI @function_tool).
    """

    name: str
    description: str
    fn: Callable[..., str]
    parameters: dict[str, Any] = field(default_factory=dict)


def parse_structured_output(raw: str, model_cls: type[BaseModel]) -> dict[str, Any] | None:
    """Extract and validate a Pydantic model from raw text containing JSON."""
    text = str(raw or "").strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return model_cls.model_validate(parsed).model_dump()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return model_cls.model_validate(json.loads(match.group(0))).model_dump()
        except Exception:
            pass
    return None


@runtime_checkable
class ExecutionEngine(Protocol):
    """Protocol that all execution engines must implement."""

    @property
    def engine_id(self) -> str: ...

    def build_llm(self, config: EngineLLMConfig) -> Any: ...

    def build_agent(
        self,
        *,
        spec: EngineAgentSpec,
        llm: Any,
        tools: list[Any],
        verbose: bool = False,
    ) -> Any: ...

    def build_tools(
        self,
        *,
        profile: str,
        defs: list[GenericToolDef],
    ) -> list[Any]: ...

    def execute_task(
        self,
        *,
        task: EngineTaskSpec,
        agent: Any,
        llm: Any,
        verbose: bool = False,
    ) -> EngineTaskResult: ...

    def execute_crew(
        self,
        *,
        tasks: list[EngineTaskSpec],
        agents: list[Any],
        llm: Any,
        process: str = "sequential",
        verbose: bool = False,
    ) -> list[EngineTaskResult]: ...
