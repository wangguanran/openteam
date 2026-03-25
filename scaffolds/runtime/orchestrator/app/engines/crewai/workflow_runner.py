from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app import agent_factory
from app import llm_factory
from app import engine_runtime
from app import task_registry
from app import workflow_registry as workflow_registry
from app.pydantic_compat import BaseModel
from app.task_models import (
    DeliveryAuditResult,
    DeliveryBugReproResult,
    DeliveryBugTestCaseResult,
    DeliveryDocumentationResult,
    DeliveryImplementationResult,
    DeliveryQAResult,
    DeliveryReviewResult,
)
from app.workflow_models import ProposalDiscussionResponse
from app.workflow_models import StructuredBugScanResult
from app.workflow_models import UpgradeFinding
from app.workflow_models import UpgradePlan
from app.workflow_models import UpgradeWorkItem


@dataclass
class WorkflowRunContext:
    db: Any
    workflow: workflow_registry.WorkflowSpec
    actor: str
    project_id: str
    workstream_id: str
    target_id: str = ""
    task_id: str = ""
    dry_run: bool = False
    force: bool = False
    run_id: str = ""
    crewai_info: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


def _workflow_spec_payload(workflow: workflow_registry.WorkflowSpec) -> dict[str, Any]:
    return {
        "team_id": workflow.team_id,
        "workflow_id": workflow.workflow_id,
        "lane": workflow.lane,
        "phase": workflow.phase,
        "display_name_zh": workflow.display_name_zh,
        "description": workflow.description,
        "enabled": workflow.enabled,
        "disabled_reason": workflow.disabled_reason,
        "task_source": workflow.task_source,
        "requires_user_confirmation": workflow.requires_user_confirmation,
        "materialize_requires_approval": workflow.materialize_requires_approval,
        "default_version_bump": workflow.default_version_bump,
        "runtime_policy": {
            "max_candidates": workflow.max_candidates(),
            "cooldown_hours": workflow.cooldown_hours(),
            "active_window_start_hour": workflow.active_window_start_hour(),
            "active_window_end_hour": workflow.active_window_end_hour(),
            "max_continuous_runtime_minutes": workflow.max_continuous_runtime_minutes(),
            "dormant_after_zero_scans": workflow.dormant_after_zero_scans(),
            "baseline_action_default": workflow.baseline_action_default,
            "baseline_action_by_bump": {key: value for key, value in workflow.baseline_action_by_bump},
        },
        "loop": {
            "enabled": workflow.loop.enabled,
            "interval_sec": workflow.loop.interval_sec,
            "initial_delay_sec": workflow.loop.initial_delay_sec,
            "concurrency": workflow.loop.concurrency,
            "run_on_startup": workflow.loop.run_on_startup,
            "target_selector": workflow.loop.target_selector,
            "max_units_per_tick": workflow.loop.max_units_per_tick,
        },
        "process": {
            "mode": workflow.process.mode,
            "verbose": workflow.process.verbose,
        },
    }


def _get_path(root: Any, path: str) -> Any:
    current = root
    for part in [segment for segment in str(path or "").split(".") if segment]:
        if isinstance(current, dict):
            current = current.get(part)
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except Exception:
                return None
            continue
        current = getattr(current, part, None)
    return current


def _resolve_value(value: Any, state: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _get_path(state, value[1:])
    if isinstance(value, dict):
        return {str(key): _resolve_value(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, state) for item in value]
    return value


def _resolve_inputs(task: workflow_registry.WorkflowTaskSpec, state: dict[str, Any]) -> dict[str, Any]:
    return {key: _resolve_value(value, state) for key, value in task.inputs}


def _task_should_run(task: workflow_registry.WorkflowTaskSpec, state: dict[str, Any]) -> bool:
    resolved = _resolve_value(task.when, state)
    if isinstance(resolved, bool):
        return resolved
    if resolved is None:
        return False
    if isinstance(resolved, (int, float)):
        return bool(resolved)
    if isinstance(resolved, str):
        text = resolved.strip().lower()
        if text in {"", "0", "false", "no", "off", "null", "none"}:
            return False
    return bool(resolved)


def _format_from_template(template: str, values: dict[str, Any]) -> str:
    if not template:
        return ""
    normalized = {key: json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value) for key, value in values.items()}
    try:
        return template.format(**normalized)
    except Exception:
        return template


def _resolve_output_model(name: str) -> type[BaseModel] | None:
    wanted = str(name or "").strip()
    if not wanted:
        return None
    if wanted in task_registry.TASK_OUTPUT_MODEL_MAP:
        return task_registry.TASK_OUTPUT_MODEL_MAP[wanted]
    runtime_models = {
        "UpgradePlan": UpgradePlan,
        "StructuredBugScanResult": StructuredBugScanResult,
        "ProposalDiscussionResponse": ProposalDiscussionResponse,
        "UpgradeFinding": UpgradeFinding,
        "UpgradeWorkItem": UpgradeWorkItem,
        "DeliveryAuditResult": DeliveryAuditResult,
        "DeliveryBugReproResult": DeliveryBugReproResult,
        "DeliveryBugTestCaseResult": DeliveryBugTestCaseResult,
        "DeliveryDocumentationResult": DeliveryDocumentationResult,
        "DeliveryImplementationResult": DeliveryImplementationResult,
        "DeliveryQAResult": DeliveryQAResult,
        "DeliveryReviewResult": DeliveryReviewResult,
    }
    model_cls = runtime_models.get(wanted)
    if isinstance(model_cls, type) and issubclass(model_cls, BaseModel):
        return model_cls
    return None


def _coerce_output(raw_output: Any, model_cls: type[BaseModel] | None) -> tuple[str, dict[str, Any] | None]:
    raw_text = str(raw_output or "").strip()
    if model_cls is None:
        if hasattr(raw_output, "raw"):
            raw_text = str(getattr(raw_output, "raw") or "").strip() or raw_text
        return raw_text, None
    if isinstance(raw_output, model_cls):
        payload = raw_output.model_dump()
        return json.dumps(payload, ensure_ascii=False, indent=2), payload
    if hasattr(raw_output, "to_dict"):
        try:
            payload = model_cls.model_validate(raw_output.to_dict()).model_dump()
            return json.dumps(payload, ensure_ascii=False, indent=2), payload
        except Exception:
            pass
    if hasattr(raw_output, "json_dict"):
        try:
            payload = model_cls.model_validate(getattr(raw_output, "json_dict")).model_dump()
            return json.dumps(payload, ensure_ascii=False, indent=2), payload
        except Exception:
            pass
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw_text):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(raw_text[idx:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            payload = model_cls.model_validate(parsed).model_dump()
            return json.dumps(payload, ensure_ascii=False, indent=2), payload
    match = re.search(r"\{.*\}", raw_text, re.S)
    if match:
        payload = model_cls.model_validate(json.loads(match.group(0))).model_dump()
        return json.dumps(payload, ensure_ascii=False, indent=2), payload
    raise RuntimeError(f"CrewAI returned no structured output for model={model_cls.__name__}")


def _execute_skill(task: workflow_registry.WorkflowTaskSpec, *, context: WorkflowRunContext, inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from app.skill_library import executor as skill_executor

    return skill_executor.execute_skill(task.skill_id, context=context, inputs=inputs, state=state)


def _execute_crewai_task(task: workflow_registry.WorkflowTaskSpec, *, context: WorkflowRunContext, inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    engine_runtime.require_crewai_importable()
    from crewai import Crew, Process, Task

    model_cls = _resolve_output_model(task.output_model)
    template_spec = None
    if task.task_template:
        template_spec = task_registry.get_task_spec(task.task_template, team_id=context.workflow.team_id)
        if model_cls is None:
            model_cls = template_spec.output_model

    task_inputs = dict(inputs)
    agent_lookup = {agent.agent_id: agent for agent in context.workflow.agents}
    agent_spec = agent_lookup.get(task.agent_id)
    if agent_spec is None:
        raise KeyError(f"unknown agent_id={task.agent_id!r} for workflow={context.workflow.workflow_id}")

    description = str(task.description or "")
    if task.description_ref:
        description = str(_resolve_value(task.description_ref, state) or "")
    elif task.description_template:
        description = _format_from_template(task.description_template, task_inputs)
    elif template_spec is not None:
        description = template_spec.render_description(payload=str(task_inputs.get("payload") or ""))

    expected_output = str(task.expected_output or "")
    if task.expected_output_ref:
        expected_output = str(_resolve_value(task.expected_output_ref, state) or "")
    elif template_spec is not None:
        expected_output = template_spec.expected_output

    from app.engines.llm_config import build_agent_llm_config
    _agent_llm_config = build_agent_llm_config(agent_spec=agent_spec, workflow=context.workflow)
    llm = llm_factory.build_crewai_llm(workflow=context.workflow, override_config=_agent_llm_config)
    tools_by_profile = task_inputs.get("tools_by_profile") if isinstance(task_inputs.get("tools_by_profile"), dict) else None
    agent = agent_factory.build_crewai_agent(
        role_id=str(task_inputs.get("role_id") or agent_spec.role_id or "").strip(),
        team_id=context.workflow.team_id,
        template_role_id=str(task_inputs.get("template_role_id") or agent_spec.template_role_id or "").strip(),
        goal=str(task_inputs.get("goal") or agent_spec.goal or "").strip(),
        backstory=str(task_inputs.get("backstory") or agent_spec.backstory or "").strip(),
        tool_profile=str(task_inputs.get("tool_profile") or agent_spec.tool_profile or "").strip(),
        tools_by_profile=tools_by_profile,
        llm=llm,
        verbose=bool(context.workflow.process.verbose),
        allow_delegation=bool(task_inputs.get("allow_delegation", agent_spec.allow_delegation)),
    )
    task_kwargs: dict[str, Any] = {
        "name": task.task_id,
        "description": description,
        "expected_output": expected_output,
        "agent": agent,
    }
    if model_cls is not None:
        task_kwargs["output_json"] = model_cls
    if bool(task_inputs.get("markdown")):
        task_kwargs["markdown"] = True
    crew_task = Task(**task_kwargs)
    crew = Crew(agents=[agent], tasks=[crew_task], process=Process.sequential, verbose=bool(context.workflow.process.verbose))
    with engine_runtime.suppress_proxy_for_codex_oauth(model=str(getattr(llm, "model", "") or "")):
        output = crew.kickoff()
    raw_text, parsed_payload = _coerce_output(output, model_cls)
    task_payload: dict[str, Any] = {
        "ok": True,
        "kind": "crewai_task",
        "agent_id": task.agent_id,
        "role_id": str(getattr(agent, "role", "") or agent_spec.role_id),
        "raw": raw_text,
        "outputs": {"raw": raw_text},
    }
    if parsed_payload is not None:
        task_payload["outputs"]["json"] = parsed_payload
    return task_payload


def _execute_engine_task(task: workflow_registry.WorkflowTaskSpec, *, context: WorkflowRunContext, inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Execute a task via the pluggable engine interface (crewai / claude / openai_agents)."""
    from app.engines.registry import get_engine
    from app.engines.base import EngineAgentSpec, EngineTaskSpec
    engine_id = str(task.engine or "").strip() or str(context.workflow.engine or "").strip()
    engine = get_engine(engine_id)

    model_cls = _resolve_output_model(task.output_model)
    template_spec = None
    if task.task_template:
        template_spec = task_registry.get_task_spec(task.task_template, team_id=context.workflow.team_id)
        if model_cls is None:
            model_cls = template_spec.output_model

    task_inputs = dict(inputs)
    agent_lookup = {agent.agent_id: agent for agent in context.workflow.agents}
    agent_spec_raw = agent_lookup.get(task.agent_id)
    if agent_spec_raw is None:
        raise KeyError(f"unknown agent_id={task.agent_id!r} for workflow={context.workflow.workflow_id}")

    role_spec = agent_factory.get_role_spec_for_engine(
        role_id=str(task_inputs.get("role_id") or agent_spec_raw.role_id or "").strip(),
        team_id=context.workflow.team_id,
        template_role_id=str(task_inputs.get("template_role_id") or agent_spec_raw.template_role_id or "").strip(),
    )

    description = str(task.description or "")
    if task.description_ref:
        description = str(_resolve_value(task.description_ref, state) or "")
    elif task.description_template:
        description = _format_from_template(task.description_template, task_inputs)
    elif template_spec is not None:
        description = template_spec.render_description(payload=str(task_inputs.get("payload") or ""))

    expected_output = str(task.expected_output or "")
    if task.expected_output_ref:
        expected_output = str(_resolve_value(task.expected_output_ref, state) or "")
    elif template_spec is not None:
        expected_output = template_spec.expected_output

    agent_spec = EngineAgentSpec(
        role_id=role_spec.role_id,
        goal=str(task_inputs.get("goal") or agent_spec_raw.goal or role_spec.goal or "").strip(),
        backstory=str(task_inputs.get("backstory") or agent_spec_raw.backstory or role_spec.backstory or "").strip(),
        tool_profile=str(task_inputs.get("tool_profile") or agent_spec_raw.tool_profile or role_spec.tool_profile or "").strip(),
        allow_delegation=bool(task_inputs.get("allow_delegation", agent_spec_raw.allow_delegation)),
    )

    from app.engines.llm_config import build_agent_llm_config
    llm_config = build_agent_llm_config(agent_spec=agent_spec_raw, workflow=context.workflow)
    llm = engine.build_llm(llm_config)

    raw_defs = task_inputs.get("generic_tool_defs") or []
    tools = engine.build_tools(profile=agent_spec.tool_profile, defs=raw_defs)

    agent = engine.build_agent(
        spec=agent_spec, llm=llm, tools=tools, verbose=bool(context.workflow.process.verbose),
    )

    engine_task = EngineTaskSpec(
        name=task.task_id,
        description=description,
        expected_output=expected_output,
        agent_spec=agent_spec,
        output_model=model_cls,
    )
    result = engine.execute_task(
        task=engine_task, agent=agent, llm=llm, verbose=bool(context.workflow.process.verbose),
    )
    return result.to_workflow_payload()


def run_workflow(*, context: WorkflowRunContext, initial_inputs: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    initial = dict(initial_inputs or {})
    state: dict[str, Any] = {
        "workflow": _workflow_spec_payload(context.workflow),
        "run": {
            "actor": context.actor,
            "project_id": context.project_id,
            "workstream_id": context.workstream_id,
            "target_id": context.target_id,
            "task_id": context.task_id,
            "dry_run": context.dry_run,
            "force": context.force,
            "run_id": context.run_id,
        },
        "inputs": initial,
        "tasks": {},
    }
    completed: list[dict[str, Any]] = []
    overall_ok = True
    stopped = False
    stop_reason = ""

    for task in context.workflow.tasks:
        if not _task_should_run(task, state):
            state["tasks"][task.task_id] = {
                "ok": True,
                "skipped": True,
                "kind": task.kind,
                "outputs": {},
            }
            continue
        inputs = _resolve_inputs(task, state)
        try:
            if task.kind == "skill":
                result = _execute_skill(task, context=context, inputs=inputs, state=state)
            elif task.kind == "action":
                result = _execute_skill(task, context=context, inputs=inputs, state=state)
            elif task.kind == "crewai_task":
                result = _execute_crewai_task(task, context=context, inputs=inputs, state=state)
            elif task.kind == "engine_task":
                result = _execute_engine_task(task, context=context, inputs=inputs, state=state)
            else:
                raise KeyError(f"unsupported workflow task kind: {task.kind}")
        except Exception as exc:
            result = {
                "ok": False,
                "kind": task.kind,
                "error": f"{type(exc).__name__}: {exc}",
                "outputs": {},
            }
            if not task.continue_on_error:
                overall_ok = False
                state["tasks"][task.task_id] = result
                completed.append({"task_id": task.task_id, **result})
                break
        normalized = dict(result)
        normalized.setdefault("ok", True)
        normalized.setdefault("kind", task.kind)
        normalized.setdefault("outputs", {})
        control = normalized.pop("control", {}) if isinstance(normalized.get("control"), dict) else {}
        state["tasks"][task.task_id] = normalized
        completed.append({"task_id": task.task_id, **normalized})
        if not bool(normalized.get("ok")):
            overall_ok = False
        if bool(control.get("stop")):
            stopped = True
            stop_reason = str(control.get("reason") or "").strip()
            break

    return {
        "ok": overall_ok,
        "workflow_id": context.workflow.workflow_id,
        "stopped": stopped,
        "stop_reason": stop_reason,
        "tasks": completed,
        "state": state,
        "final_outputs": dict(state.get("tasks") or {}),
    }
