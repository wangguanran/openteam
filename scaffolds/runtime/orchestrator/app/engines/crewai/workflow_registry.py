from __future__ import annotations

import datetime as _dt
import os
import re as _re
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from app import spec_loader
from app import improvement_store
from app import project_config_store
from app.engines.crewai import team_registry


PHASE_FINDING = "finding"
PHASE_DISCUSSION = "discussion"
PHASE_CODING = "coding"


def _default_team_id() -> str:
    return team_registry.default_team_id()

def _workflow_now_local() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _to_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _to_int(raw: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _normalize_workflow_id(raw: str) -> str:
    return str(raw or "").strip()


def _workflow_aliases(workflow_id: str, lane: str = "") -> tuple[str, ...]:
    canonical = _normalize_workflow_id(workflow_id)
    return (canonical,) if canonical else ()


def _canonical_workflow_id(workflow_id: str) -> str:
    return _normalize_workflow_id(workflow_id)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged.get(key) or {}), value)
        else:
            merged[key] = value
    return merged


def _workflow_override_keys(workflow_id: str, lane: str = "") -> tuple[str, ...]:
    aliases = _workflow_aliases(workflow_id, lane=lane)
    unique: list[str] = []
    for alias in aliases:
        text = str(alias or "").strip()
        if text and text not in unique:
            unique.append(text)
    return tuple(unique)


def _team_workflow_override(team_id: str, workflow_id: str, *, lane: str = "") -> dict[str, Any]:
    team = spec_loader.team_doc(team_id)
    raw = team.get("workflow_settings") or {}
    if not isinstance(raw, dict):
        return {}
    for key in _workflow_override_keys(workflow_id, lane=lane):
        override = raw.get(key) or {}
        if isinstance(override, dict) and override:
            return dict(override)
    return {}


def _project_workflow_override(project_id: str, team_id: str, workflow_id: str, *, lane: str = "") -> dict[str, Any]:
    config = project_config_store.load_project_config(str(project_id or "").strip() or "openteam")
    teams = config.get("teams") or {}
    if not isinstance(teams, dict):
        return {}
    team = teams.get(str(team_id or "").strip()) or {}
    if not isinstance(team, dict):
        return {}
    raw = team.get("workflow_settings") or {}
    if not isinstance(raw, dict):
        return {}
    for key in _workflow_override_keys(workflow_id, lane=lane):
        override = raw.get(key) or {}
        if isinstance(override, dict) and override:
            return dict(override)
    return {}


@dataclass(frozen=True)
class WorkflowRunPolicy:
    allowed: bool
    reason: str = ""
    active_window_start_hour: int = 0
    active_window_end_hour: int = 24
    max_continuous_runtime_minutes: int = 0
    current_local_hour: int = 0
    active_since: str = ""
    now_iso: str = ""


@dataclass(frozen=True)
class WorkflowLoopSpec:
    enabled: bool = True
    interval_sec: int = 300
    initial_delay_sec: int = 30
    concurrency: int = 1
    run_on_startup: bool = False
    target_selector: str = "enabled_targets"
    max_units_per_tick: int = 1


@dataclass(frozen=True)
class WorkflowProcessSpec:
    mode: str = "sequential"
    verbose: bool = False


@dataclass(frozen=True)
class WorkflowAgentSpec:
    agent_id: str
    role_id: str
    tool_profile: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    max_tokens: int = 0
    allow_delegation: bool = False
    template_role_id: str = ""
    goal: str = ""
    backstory: str = ""


@dataclass(frozen=True)
class WorkflowTaskSpec:
    task_id: str
    kind: str
    engine: str = ""  # "crewai" | "claude" | "openai_agents" | "" (inherit from workflow)
    skill_id: str = ""
    agent_id: str = ""
    task_template: str = ""
    description: str = ""
    description_template: str = ""
    description_ref: str = ""
    expected_output: str = ""
    expected_output_ref: str = ""
    output_model: str = ""
    inputs: tuple[tuple[str, Any], ...] = ()
    when: Any = True
    continue_on_error: bool = False


@dataclass(frozen=True)
class WorkflowSpec:
    team_id: str
    workflow_id: str
    lane: str
    engine: str = ""  # default engine for all tasks; "" = env OPENTEAM_ENGINE or "crewai"
    phase: str = ""
    display_name_zh: str = ""
    description: str = ""
    enabled: bool = True
    disabled_reason: str = ""
    stages: tuple[str, ...] = ()
    task_source: str = "proposal"
    requires_user_confirmation: bool = False
    materialize_requires_approval: bool = False
    materialize_blocked_statuses: tuple[str, ...] = ("REJECTED", "HOLD", "MATERIALIZED")
    default_version_bump: str = "none"
    default_max_candidates: int = 0
    default_active_window_start_hour: int = 0
    default_active_window_end_hour: int = 24
    default_max_continuous_runtime_minutes: int = 0
    default_dormant_after_zero_scans: int = 0
    default_cooldown_hours: int = 0
    baseline_action_default: str = ""
    baseline_action_by_bump: tuple[tuple[str, str], ...] = ()
    loop: WorkflowLoopSpec = WorkflowLoopSpec()
    process: WorkflowProcessSpec = WorkflowProcessSpec()
    agents: tuple[WorkflowAgentSpec, ...] = ()
    tasks: tuple[WorkflowTaskSpec, ...] = ()

    @property
    def uses_proposal(self) -> bool:
        return self.task_source != "direct_task"

    def cooldown_hours(self) -> int:
        return max(0, int(self.default_cooldown_hours))

    def max_candidates(self) -> int:
        return max(0, int(self.default_max_candidates))

    def active_window_start_hour(self) -> int:
        return min(23, max(0, int(self.default_active_window_start_hour)))

    def active_window_end_hour(self) -> int:
        return min(24, max(0, int(self.default_active_window_end_hour)))

    def max_continuous_runtime_minutes(self) -> int:
        return max(0, int(self.default_max_continuous_runtime_minutes))

    def dormant_after_zero_scans(self) -> int:
        return max(0, int(self.default_dormant_after_zero_scans))

    def default_baseline_action(self, version_bump: str) -> str:
        bump = str(version_bump or "").strip().lower()
        mapping = {str(k).strip().lower(): str(v).strip() for k, v in self.baseline_action_by_bump}
        return mapping.get(bump) or self.baseline_action_default

    def should_materialize(self, *, status: str, due: bool) -> bool:
        if not self.enabled:
            return False
        if self.task_source == "direct_task":
            return True
        if not due:
            return False
        normalized_status = str(status or "").strip().upper()
        if self.materialize_requires_approval:
            return normalized_status == "APPROVED"
        blocked = {str(item).strip().upper() for item in self.materialize_blocked_statuses}
        return normalized_status not in blocked

    def evaluate_run_policy(
        self,
        *,
        state: dict[str, Any] | None = None,
        force: bool = False,
        now: _dt.datetime | None = None,
    ) -> WorkflowRunPolicy:
        current = (now or _workflow_now_local()).astimezone()
        now_iso = current.replace(microsecond=0).isoformat()
        if force:
            return WorkflowRunPolicy(
                allowed=True,
                reason="force",
                active_window_start_hour=self.active_window_start_hour(),
                active_window_end_hour=self.active_window_end_hour(),
                max_continuous_runtime_minutes=self.max_continuous_runtime_minutes(),
                current_local_hour=int(current.hour),
                active_since=now_iso,
                now_iso=now_iso,
            )

        state_doc = dict(state or {})
        active_since = str(state_doc.get("active_since") or "").strip()
        active_since_dt: _dt.datetime | None = None
        if active_since:
            try:
                active_since_dt = _dt.datetime.fromisoformat(active_since.replace("Z", "+00:00")).astimezone()
            except Exception:
                active_since = ""
                active_since_dt = None
        if active_since_dt is None:
            active_since_dt = current
            active_since = now_iso

        max_minutes = self.max_continuous_runtime_minutes()
        if max_minutes > 0:
            elapsed_minutes = max(0.0, (current - active_since_dt).total_seconds() / 60.0)
            if elapsed_minutes >= float(max_minutes):
                return WorkflowRunPolicy(
                    allowed=False,
                    reason="max_continuous_runtime_exceeded",
                    active_window_start_hour=self.active_window_start_hour(),
                    active_window_end_hour=self.active_window_end_hour(),
                    max_continuous_runtime_minutes=max_minutes,
                    current_local_hour=int(current.hour),
                    active_since=active_since,
                    now_iso=now_iso,
                )

        start_hour = self.active_window_start_hour()
        end_hour = self.active_window_end_hour()
        current_hour = int(current.hour)
        if start_hour == end_hour:
            in_window = True
        elif start_hour < end_hour:
            in_window = start_hour <= current_hour < end_hour
        else:
            in_window = current_hour >= start_hour or current_hour < end_hour
        if not in_window:
            return WorkflowRunPolicy(
                allowed=False,
                reason="outside_active_window",
                active_window_start_hour=start_hour,
                active_window_end_hour=end_hour,
                max_continuous_runtime_minutes=self.max_continuous_runtime_minutes(),
                current_local_hour=current_hour,
                active_since="",
                now_iso=now_iso,
            )

        return WorkflowRunPolicy(
            allowed=True,
            reason="active",
            active_window_start_hour=start_hour,
            active_window_end_hour=end_hour,
            max_continuous_runtime_minutes=max_minutes,
            current_local_hour=current_hour,
            active_since=active_since,
            now_iso=now_iso,
        )


def _loop_spec_from_doc(doc: dict[str, Any]) -> WorkflowLoopSpec:
    return WorkflowLoopSpec(
        enabled=_to_bool(doc.get("enabled"), True),
        interval_sec=_to_int(doc.get("interval_sec"), 300, minimum=0),
        initial_delay_sec=_to_int(doc.get("initial_delay_sec"), 30, minimum=0),
        concurrency=_to_int(doc.get("concurrency"), 1, minimum=1),
        run_on_startup=_to_bool(doc.get("run_on_startup"), False),
        target_selector=str(doc.get("target_selector") or "enabled_targets").strip() or "enabled_targets",
        max_units_per_tick=_to_int(doc.get("max_units_per_tick"), 1, minimum=1),
    )


def _process_spec_from_doc(doc: dict[str, Any]) -> WorkflowProcessSpec:
    return WorkflowProcessSpec(
        mode=str(doc.get("mode") or "sequential").strip() or "sequential",
        verbose=_to_bool(doc.get("verbose"), False),
    )


def _expand_env(value: str) -> str:
    """Expand ${ENV_VAR} references. Unresolved vars become empty string."""
    return _re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), str(value or ""))


def _agent_spec_from_doc(raw: dict[str, Any]) -> WorkflowAgentSpec:
    return WorkflowAgentSpec(
        agent_id=str(raw.get("agent_id") or raw.get("id") or "").strip(),
        role_id=str(raw.get("role_id") or raw.get("role") or "").strip(),
        tool_profile=str(raw.get("tool_profile") or "").strip(),
        model=str(raw.get("model") or "").strip(),
        base_url=_expand_env(str(raw.get("base_url") or "").strip()),
        api_key=_expand_env(str(raw.get("api_key") or "").strip()),
        max_tokens=_to_int(raw.get("max_tokens"), 0, minimum=0),
        allow_delegation=_to_bool(raw.get("allow_delegation"), False),
        template_role_id=str(raw.get("template_role_id") or "").strip(),
        goal=str(raw.get("goal") or "").strip(),
        backstory=str(raw.get("backstory") or "").strip(),
    )


def _task_spec_from_doc(raw: dict[str, Any]) -> WorkflowTaskSpec:
    inputs = raw.get("inputs") or {}
    input_items: list[tuple[str, Any]] = []
    if isinstance(inputs, dict):
        input_items = [(str(key), value) for key, value in inputs.items()]
    return WorkflowTaskSpec(
        task_id=str(raw.get("task_id") or raw.get("id") or "").strip(),
        kind=str(raw.get("kind") or "skill").strip() or "skill",
        engine=str(raw.get("engine") or "").strip(),
        skill_id=str(raw.get("skill_id") or raw.get("action_id") or "").strip(),
        agent_id=str(raw.get("agent_id") or "").strip(),
        task_template=str(raw.get("task_template") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        description_template=str(raw.get("description_template") or "").strip(),
        description_ref=str(raw.get("description_ref") or "").strip(),
        expected_output=str(raw.get("expected_output") or "").strip(),
        expected_output_ref=str(raw.get("expected_output_ref") or "").strip(),
        output_model=str(raw.get("output_model") or "").strip(),
        inputs=tuple(input_items),
        when=raw.get("when", True),
        continue_on_error=_to_bool(raw.get("continue_on_error"), False),
    )


def _workflow_spec_from_doc(doc: dict[str, Any]) -> WorkflowSpec:
    baseline_raw = doc.get("baseline_action_by_bump") or {}
    baseline_items: list[tuple[str, str]] = []
    if isinstance(baseline_raw, dict):
        baseline_items = [(str(key).strip(), str(value).strip()) for key, value in baseline_raw.items()]

    agent_docs = list(doc.get("agents") or [])
    task_docs = list(doc.get("tasks") or [])
    agents = tuple(
        _agent_spec_from_doc(item)
        for item in agent_docs
        if isinstance(item, dict) and str(item.get("agent_id") or item.get("id") or "").strip()
    )
    tasks = tuple(
        _task_spec_from_doc(item)
        for item in task_docs
        if isinstance(item, dict) and str(item.get("task_id") or item.get("id") or "").strip()
    )

    runtime_policy = dict(doc.get("runtime_policy") or {}) if isinstance(doc.get("runtime_policy"), dict) else {}
    loop_doc = dict(doc.get("loop") or {}) if isinstance(doc.get("loop"), dict) else {}
    process_doc = dict(doc.get("process") or {}) if isinstance(doc.get("process"), dict) else {}
    return WorkflowSpec(
        team_id=str(doc.get("team_id") or "").strip() or _default_team_id(),
        workflow_id=str(doc.get("workflow_id") or "").strip(),
        lane=str(doc.get("lane") or "").strip().lower(),
        engine=str(doc.get("engine") or "").strip(),
        phase=str(doc.get("phase") or "").strip().lower(),
        display_name_zh=str(doc.get("display_name_zh") or "").strip(),
        description=str(doc.get("description") or "").strip(),
        enabled=_to_bool(doc.get("enabled"), True),
        disabled_reason=str(doc.get("disabled_reason") or "").strip(),
        stages=tuple(str(item).strip() for item in list(doc.get("stages") or []) if str(item).strip()),
        task_source=str(doc.get("task_source") or "proposal").strip() or "proposal",
        requires_user_confirmation=_to_bool(doc.get("requires_user_confirmation"), False),
        materialize_requires_approval=_to_bool(doc.get("materialize_requires_approval"), False),
        materialize_blocked_statuses=tuple(
            str(item).strip() for item in list(doc.get("materialize_blocked_statuses") or []) if str(item).strip()
        )
        or ("REJECTED", "HOLD", "MATERIALIZED"),
        default_version_bump=str(doc.get("default_version_bump") or "none").strip() or "none",
        default_max_candidates=_to_int(runtime_policy.get("max_candidates", doc.get("default_max_candidates", 0)), 0, minimum=0),
        default_active_window_start_hour=_to_int(runtime_policy.get("active_window_start_hour", doc.get("default_active_window_start_hour", 0)), 0, minimum=0, maximum=23),
        default_active_window_end_hour=_to_int(runtime_policy.get("active_window_end_hour", doc.get("default_active_window_end_hour", 24)), 24, minimum=0, maximum=24),
        default_max_continuous_runtime_minutes=_to_int(runtime_policy.get("max_continuous_runtime_minutes", doc.get("default_max_continuous_runtime_minutes", 0)), 0, minimum=0),
        default_dormant_after_zero_scans=_to_int(runtime_policy.get("dormant_after_zero_scans", doc.get("default_dormant_after_zero_scans", 0)), 0, minimum=0),
        default_cooldown_hours=_to_int(runtime_policy.get("cooldown_hours", doc.get("default_cooldown_hours", 0)), 0, minimum=0),
        baseline_action_default=str(doc.get("baseline_action_default") or "").strip(),
        baseline_action_by_bump=tuple(baseline_items),
        loop=_loop_spec_from_doc(loop_doc),
        process=_process_spec_from_doc(process_doc),
        agents=agents,
        tasks=tasks,
    )


def _workflow_doc_with_overrides(team_id: str, workflow_id: str, *, project_id: str = "openteam") -> dict[str, Any]:
    canonical = _canonical_workflow_id(workflow_id)
    loaded = spec_loader.team_workflow_doc(team_id, canonical)
    if not loaded:
        raise KeyError(f"unknown workflow spec: {workflow_id}")
    lane = str(loaded.get("lane") or "").strip().lower()
    team = spec_loader.team_doc(team_id)
    workflow_ids = {str(item).strip() for item in list(team.get("workflow_ids") or []) if str(item).strip()}
    base_doc = dict(loaded)
    if workflow_ids and not any(key in workflow_ids for key in _workflow_override_keys(canonical, lane=lane)):
        base_doc["enabled"] = False
        base_doc.setdefault("disabled_reason", "workflow_not_in_team_workflow_ids")

    merged = _deep_merge(base_doc, _team_workflow_override(team_id, canonical, lane=lane))
    merged = _deep_merge(merged, _project_workflow_override(project_id, team_id, canonical, lane=lane))
    merged["team_id"] = str(team_id or "").strip()
    return merged


def list_workflows(*, team_id: str = "", project_id: str = "openteam") -> tuple[WorkflowSpec, ...]:
    resolved_team_id = str(team_id or "").strip() or _default_team_id()
    out: list[WorkflowSpec] = []
    for doc in spec_loader.list_team_workflow_docs(resolved_team_id):
        workflow_id = str(doc.get("workflow_id") or "").strip()
        if not workflow_id:
            continue
        merged = _workflow_doc_with_overrides(resolved_team_id, workflow_id, project_id=project_id)
        out.append(_workflow_spec_from_doc(merged))
    return tuple(out)


def workflow_spec(workflow_id: str, *, team_id: str = "", project_id: str = "openteam") -> WorkflowSpec:
    wanted = _canonical_workflow_id(workflow_id)
    if not wanted:
        raise KeyError("workflow_id is required")
    resolved_team_id = str(team_id or "").strip() or _default_team_id()
    merged = _workflow_doc_with_overrides(resolved_team_id, wanted, project_id=project_id)
    return _workflow_spec_from_doc(merged)


def workflow_for_phase(phase: str, *, team_id: str = "", project_id: str = "openteam") -> WorkflowSpec:
    normalized_phase = str(phase or PHASE_FINDING).strip().lower() or PHASE_FINDING
    resolved_team_id = str(team_id or "").strip() or _default_team_id()
    for spec in list_workflows(team_id=resolved_team_id, project_id=project_id):
        if spec.phase == normalized_phase:
            return spec
    raise KeyError(f"unknown workflow phase={normalized_phase!r}")


def workflow_for_lane_phase(lane: str, phase: str, *, team_id: str = "", project_id: str = "openteam") -> WorkflowSpec:
    normalized_lane = str(lane or "").strip().lower() or "bug"
    normalized_phase = str(phase or PHASE_FINDING).strip().lower() or PHASE_FINDING
    resolved_team_id = str(team_id or "").strip() or _default_team_id()
    if normalized_phase == PHASE_CODING:
        return workflow_for_phase(PHASE_CODING, team_id=resolved_team_id, project_id=project_id)
    workflows = list_workflows(team_id=resolved_team_id, project_id=project_id)
    # Exact match
    for spec in workflows:
        if spec.lane == normalized_lane and spec.phase == normalized_phase:
            return spec
    # Fallback: "review" lane covers all finding lanes (bug/feature/quality/process)
    if normalized_phase == PHASE_FINDING:
        for spec in workflows:
            if spec.lane == "review" and spec.phase == PHASE_FINDING:
                return spec
    if normalized_phase == PHASE_DISCUSSION and normalized_lane in ("bug", "process"):
        raise KeyError(f"lane {normalized_lane!r} does not support phase {normalized_phase!r}")
    for spec in workflows:
        if spec.lane == normalized_lane and spec.phase == PHASE_FINDING:
            return spec
    # Final fallback: review workflow
    for spec in workflows:
        if spec.lane == "review":
            return spec
    raise KeyError(f"unknown workflow for lane={normalized_lane!r} phase={normalized_phase!r}")


def workflow_for_lane(lane: str, *, team_id: str = "", project_id: str = "openteam") -> WorkflowSpec:
    return workflow_for_lane_phase(lane, PHASE_FINDING, team_id=team_id, project_id=project_id)


def _workflow_states(target_id: str) -> dict[str, Any]:
    state = improvement_store.load_target_state(str(target_id or "").strip())
    raw = state.get("workflow_states")
    return dict(raw) if isinstance(raw, dict) else {}


def workflow_runtime_state(target_id: str, workflow_id: str) -> dict[str, Any]:
    state = _workflow_states(target_id)
    workflow_state = state.get(_canonical_workflow_id(workflow_id))
    return dict(workflow_state) if isinstance(workflow_state, dict) else {}


def update_workflow_runtime_state(target_id: str, workflow_id: str, policy: WorkflowRunPolicy) -> dict[str, Any]:
    target_key = str(target_id or "").strip()
    workflow_key = _canonical_workflow_id(workflow_id)
    state = improvement_store.load_target_state(target_key)
    workflow_states = dict(state.get("workflow_states") or {}) if isinstance(state.get("workflow_states"), dict) else {}
    current = dict(workflow_states.get(workflow_key) or {}) if isinstance(workflow_states.get(workflow_key), dict) else {}
    active_since = str(policy.active_since or current.get("active_since") or "").strip()
    if not policy.allowed and str(policy.reason or "") == "outside_active_window":
        active_since = ""
    current.update(
        {
            "status": "active" if policy.allowed else "paused",
            "active_since": active_since,
            "last_reason": str(policy.reason or ""),
            "last_evaluated_at": str(policy.now_iso or ""),
            "last_allowed_at": str(policy.now_iso or "") if policy.allowed else str(current.get("last_allowed_at") or ""),
            "last_blocked_at": str(policy.now_iso or "") if not policy.allowed else str(current.get("last_blocked_at") or ""),
            "active_window_start_hour": int(policy.active_window_start_hour),
            "active_window_end_hour": int(policy.active_window_end_hour),
            "max_continuous_runtime_minutes": int(policy.max_continuous_runtime_minutes),
            "current_local_hour": int(policy.current_local_hour),
        }
    )
    workflow_states[workflow_key] = current
    state["workflow_states"] = workflow_states
    improvement_store.save_target_state(target_key, state)
    return current


def evaluate_workflow_runtime_policy(
    *,
    workflow: WorkflowSpec,
    target_id: str,
    force: bool = False,
    now: _dt.datetime | None = None,
) -> WorkflowRunPolicy:
    return workflow.evaluate_run_policy(
        state=workflow_runtime_state(target_id, workflow.workflow_id),
        force=force,
        now=now,
    )
