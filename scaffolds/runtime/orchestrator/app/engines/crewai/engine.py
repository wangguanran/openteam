"""CrewAI execution engine implementation."""
from __future__ import annotations

from typing import Any

from app.engines.base import (
    EngineAgentSpec,
    EngineLLMConfig,
    EngineTaskResult,
    EngineTaskSpec,
    GenericToolDef,
    parse_structured_output,
)


class CrewAIEngine:
    @property
    def engine_id(self) -> str:
        return "crewai"

    def build_llm(self, config: EngineLLMConfig) -> Any:
        from app import engine_runtime

        engine_runtime.require_crewai_importable(refresh=True)
        from crewai.llm import LLM

        from app.engines.provider import detect_provider, is_reasoning_model

        gateway = str((config.extra or {}).get("gateway") or "").strip().lower()
        provider = detect_provider(config.model, gateway=gateway)
        # Direct OpenRouter still passes through litellm; LiteLLM proxy mode bypasses
        # provider-specific auth wiring and routes everything through the global gateway.
        model_for_llm = config.model
        if provider.name == "openrouter" and config.api_key and gateway != "litellm_proxy":
            import os
            os.environ.setdefault("OPENROUTER_API_KEY", config.api_key)
        kwargs: dict[str, Any] = {
            "model": model_for_llm,
            "max_tokens": config.max_tokens,
            "is_litellm": provider.litellm,
        }
        if provider.api_mode == "responses":
            kwargs["api"] = "responses"
        if config.max_retries:
            kwargs["max_retries"] = config.max_retries
        if provider.supports_reasoning and is_reasoning_model(config.model):
            kwargs["reasoning_effort"] = config.reasoning_effort
        if config.base_url or provider.default_base_url:
            kwargs["base_url"] = config.base_url or provider.default_base_url
        if config.api_key:
            kwargs["api_key"] = config.api_key
        return LLM(**kwargs)

    def build_agent(
        self,
        *,
        spec: EngineAgentSpec,
        llm: Any,
        tools: list[Any],
        verbose: bool = False,
    ) -> Any:
        from crewai import Agent

        return Agent(
            role=spec.role_id,
            goal=spec.goal,
            backstory=spec.backstory,
            llm=llm,
            tools=tools,
            allow_delegation=spec.allow_delegation,
            verbose=verbose,
        )

    def build_tools(
        self,
        *,
        profile: str,
        defs: list[GenericToolDef],
    ) -> list[Any]:
        from app.engines.crewai.tool_adapter import generic_to_crewai_tools

        return generic_to_crewai_tools(defs)

    def execute_task(
        self,
        *,
        task: EngineTaskSpec,
        agent: Any,
        llm: Any,
        verbose: bool = False,
    ) -> EngineTaskResult:
        from app import engine_runtime
        from crewai import Crew, Process, Task

        task_kwargs: dict[str, Any] = {
            "name": task.name,
            "description": task.description,
            "expected_output": task.expected_output,
            "agent": agent,
        }
        if task.output_model is not None:
            task_kwargs["output_json"] = task.output_model

        crew_task = Task(**task_kwargs)
        crew = Crew(
            agents=[agent],
            tasks=[crew_task],
            process=Process.sequential,
            verbose=verbose,
        )
        model_str = str(getattr(llm, "model", "") or "")
        with engine_runtime.suppress_proxy_for_codex_oauth(model=model_str):
            output = crew.kickoff()

        raw_text = self._extract_raw(output)
        parsed = None
        if task.output_model is not None:
            parsed = self._parse_crewai_output(output, task.output_model)
            if parsed is None:
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
        from app import engine_runtime
        from crewai import Crew, Process, Task

        crew_tasks: list[Any] = []
        for idx, t in enumerate(tasks):
            kwargs: dict[str, Any] = {
                "name": t.name,
                "description": t.description,
                "expected_output": t.expected_output,
                "agent": agents[idx] if idx < len(agents) else agents[-1],
            }
            if t.output_model is not None:
                kwargs["output_json"] = t.output_model
            crew_tasks.append(Task(**kwargs))

        proc = Process.sequential if process == "sequential" else Process.hierarchical
        crew = Crew(agents=agents, tasks=crew_tasks, process=proc, verbose=verbose)
        model_str = str(getattr(llm, "model", "") or "")
        with engine_runtime.suppress_proxy_for_codex_oauth(model=model_str):
            output = crew.kickoff()

        raw_text = self._extract_raw(output)
        last_model = tasks[-1].output_model if tasks else None
        parsed = None
        if last_model is not None:
            parsed = self._parse_crewai_output(output, last_model)
            if parsed is None:
                parsed = parse_structured_output(raw_text, last_model)

        return [EngineTaskResult(ok=True, raw=raw_text, parsed=parsed)]

    @staticmethod
    def _extract_raw(output: Any) -> str:
        if hasattr(output, "raw"):
            return str(getattr(output, "raw", "") or "")
        return str(output or "")

    @staticmethod
    def _parse_crewai_output(output: Any, model_cls: type) -> dict[str, Any] | None:
        if isinstance(output, model_cls):
            return output.model_dump()
        if hasattr(output, "to_dict"):
            try:
                return model_cls.model_validate(output.to_dict()).model_dump()
            except Exception:
                pass
        if hasattr(output, "json_dict") and output.json_dict:
            try:
                return model_cls.model_validate(output.json_dict).model_dump()
            except Exception:
                pass
        return None
