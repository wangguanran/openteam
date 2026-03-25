"""Claude (Anthropic SDK) execution engine with agentic tool_use loop."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.engines.base import (
    EngineAgentSpec,
    EngineLLMConfig,
    EngineTaskResult,
    EngineTaskSpec,
    GenericToolDef,
    parse_structured_output,
)

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 20


class ClaudeEngine:
    @property
    def engine_id(self) -> str:
        return "claude"

    def build_llm(self, config: EngineLLMConfig) -> dict[str, Any]:
        """Return a config dict; the actual client is created lazily per-call."""
        return {
            "model": config.model or "claude-sonnet-4-20250514",
            "api_key": config.api_key,
            "base_url": config.base_url,
            "max_tokens": config.max_tokens,
        }

    def build_agent(
        self,
        *,
        spec: EngineAgentSpec,
        llm: Any,
        tools: list[Any],
        verbose: bool = False,
    ) -> dict[str, Any]:
        system_prompt = f"# Role: {spec.role_id}\n\n"
        if spec.goal:
            system_prompt += f"## Goal\n{spec.goal}\n\n"
        if spec.backstory:
            system_prompt += f"## Backstory\n{spec.backstory}\n"
        return {
            "system_prompt": system_prompt,
            "tools": tools,
            "llm_config": llm,
            "spec": spec,
            "verbose": verbose,
        }

    def build_tools(
        self,
        *,
        profile: str,
        defs: list[GenericToolDef],
    ) -> list[Any]:
        from app.engines.claude.tool_adapter import to_anthropic_tools
        return to_anthropic_tools(defs)

    def execute_task(
        self,
        *,
        task: EngineTaskSpec,
        agent: Any,
        llm: Any,
        verbose: bool = False,
    ) -> EngineTaskResult:
        import anthropic

        llm_config = agent["llm_config"]
        client_kwargs: dict[str, Any] = {}
        if llm_config.get("api_key"):
            client_kwargs["api_key"] = llm_config["api_key"]
        if llm_config.get("base_url"):
            client_kwargs["base_url"] = llm_config["base_url"]
        client = anthropic.Anthropic(**client_kwargs)

        model = llm_config.get("model", "claude-sonnet-4-20250514")
        max_tokens = llm_config.get("max_tokens", 4096)
        system = agent["system_prompt"]
        tools = agent["tools"]

        # Build tool name -> fn lookup for tool_use execution
        from app.engines.claude.tool_adapter import tool_name_to_fn
        raw_defs = agent.get("_generic_defs") or []
        fn_lookup = tool_name_to_fn(raw_defs) if raw_defs else {}

        user_msg = f"{task.description}\n\nExpected output format: {task.expected_output}"
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_msg}]

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        # Agentic tool_use loop
        final_text = ""
        for _round in range(_MAX_TOOL_ROUNDS):
            response = client.messages.create(**kwargs)
            stop_reason = getattr(response, "stop_reason", "end_turn")

            text_parts: list[str] = []
            tool_calls: list[Any] = []
            for block in getattr(response, "content", []):
                if getattr(block, "type", "") == "text":
                    text_parts.append(str(getattr(block, "text", "")))
                elif getattr(block, "type", "") == "tool_use":
                    tool_calls.append(block)

            if text_parts:
                final_text = "\n".join(text_parts)

            if stop_reason != "tool_use" or not tool_calls:
                break

            # Execute tool calls and feed results back
            messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            for tc in tool_calls:
                tool_name = getattr(tc, "name", "")
                tool_input = getattr(tc, "input", {}) or {}
                tool_id = getattr(tc, "id", "")
                defn = fn_lookup.get(tool_name)
                if defn is not None:
                    try:
                        result_text = defn.fn(**tool_input)
                    except Exception as e:
                        result_text = f"Error: {e}"
                else:
                    result_text = f"Unknown tool: {tool_name}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": str(result_text),
                })
                if verbose:
                    logger.info("Claude tool_use: %s -> %s chars", tool_name, len(str(result_text)))
            messages.append({"role": "user", "content": tool_results})
            kwargs["messages"] = messages

        parsed = None
        if task.output_model is not None:
            parsed = parse_structured_output(final_text, task.output_model)

        return EngineTaskResult(
            ok=True,
            raw=final_text,
            parsed=parsed,
            role_id=task.agent_spec.role_id,
        )

    def execute_crew(
        self,
        *,
        tasks: list[EngineTaskSpec],
        agents: list[Any],
        llm: Any,
        process: str = "sequential",
        verbose: bool = False,
    ) -> list[EngineTaskResult]:
        results: list[EngineTaskResult] = []
        for idx, task in enumerate(tasks):
            agent = agents[idx] if idx < len(agents) else agents[-1]
            results.append(self.execute_task(task=task, agent=agent, llm=llm, verbose=verbose))
        return results
