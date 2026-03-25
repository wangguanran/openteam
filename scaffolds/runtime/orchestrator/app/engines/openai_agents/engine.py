"""OpenAI Agents SDK execution engine."""
from __future__ import annotations

import asyncio
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


class OpenAIAgentsEngine:
    @property
    def engine_id(self) -> str:
        return "openai_agents"

    def build_llm(self, config: EngineLLMConfig) -> dict[str, Any]:
        """Return config dict; the SDK uses model name strings directly."""
        return {
            "model": config.model or "gpt-4.1",
            "api_key": config.api_key,
            "base_url": config.base_url,
        }

    def build_agent(
        self,
        *,
        spec: EngineAgentSpec,
        llm: Any,
        tools: list[Any],
        verbose: bool = False,
    ) -> Any:
        from agents import Agent

        instructions = f"# Role: {spec.role_id}\n\n"
        if spec.goal:
            instructions += f"## Goal\n{spec.goal}\n\n"
        if spec.backstory:
            instructions += f"## Backstory\n{spec.backstory}\n"

        model = llm.get("model", "gpt-4.1") if isinstance(llm, dict) else "gpt-4.1"
        return Agent(
            name=spec.role_id,
            instructions=instructions,
            model=model,
            tools=tools,
        )

    def build_tools(
        self,
        *,
        profile: str,
        defs: list[GenericToolDef],
    ) -> list[Any]:
        from app.engines.openai_agents.tool_adapter import to_agent_tools
        return to_agent_tools(defs)

    def execute_task(
        self,
        *,
        task: EngineTaskSpec,
        agent: Any,
        llm: Any,
        verbose: bool = False,
    ) -> EngineTaskResult:
        from agents import Runner

        prompt = f"{task.description}\n\nExpected output format: {task.expected_output}"

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(
                    lambda: asyncio.run(Runner.run(agent, prompt))
                ).result()
        else:
            result = asyncio.run(Runner.run(agent, prompt))

        raw_text = str(result.final_output or "")
        parsed = None
        if task.output_model is not None:
            parsed = parse_structured_output(raw_text, task.output_model)

        return EngineTaskResult(
            ok=True,
            raw=raw_text,
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
