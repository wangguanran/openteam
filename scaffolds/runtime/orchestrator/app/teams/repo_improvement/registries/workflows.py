from __future__ import annotations

import os
import datetime as _dt
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from app import crewai_role_registry
from app import improvement_store
from app import project_config_store
from app import crewai_spec_loader


def _workflow_now_local() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


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
class WorkflowSpec:
    workflow_id: str
    lane: str
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
    max_candidates_env_var: str = ""
    default_max_candidates: int = 0
    active_window_start_hour_env_var: str = ""
    default_active_window_start_hour: int = 0
    active_window_end_hour_env_var: str = ""
    default_active_window_end_hour: int = 24
    max_continuous_runtime_minutes_env_var: str = ""
    default_max_continuous_runtime_minutes: int = 0
    dormant_after_zero_scans_env_var: str = ""
    default_dormant_after_zero_scans: int = 0
    cooldown_env_var: str = ""
    default_cooldown_hours: int = 0
    baseline_action_default: str = ""
    baseline_action_by_bump: tuple[tuple[str, str], ...] = ()

    @property
    def uses_proposal(self) -> bool:
        return self.task_source != "direct_task"

    def cooldown_hours(self) -> int:
        if self.cooldown_env_var:
            raw = str(os.getenv(self.cooldown_env_var, str(self.default_cooldown_hours)) or "").strip()
            try:
                return max(0, int(raw))
            except Exception:
                return max(0, int(self.default_cooldown_hours))
        return max(0, int(self.default_cooldown_hours))

    def max_candidates(self) -> int:
        if self.max_candidates_env_var:
            raw = str(os.getenv(self.max_candidates_env_var, str(self.default_max_candidates)) or "").strip()
            try:
                return max(0, int(raw))
            except Exception:
                return max(0, int(self.default_max_candidates))
        return max(0, int(self.default_max_candidates))

    def active_window_start_hour(self) -> int:
        if self.active_window_start_hour_env_var:
            raw = str(os.getenv(self.active_window_start_hour_env_var, str(self.default_active_window_start_hour)) or "").strip()
            try:
                return min(23, max(0, int(raw)))
            except Exception:
                return min(23, max(0, int(self.default_active_window_start_hour)))
        return min(23, max(0, int(self.default_active_window_start_hour)))

    def active_window_end_hour(self) -> int:
        if self.active_window_end_hour_env_var:
            raw = str(os.getenv(self.active_window_end_hour_env_var, str(self.default_active_window_end_hour)) or "").strip()
            try:
                return min(24, max(0, int(raw)))
            except Exception:
                return min(24, max(0, int(self.default_active_window_end_hour)))
        return min(24, max(0, int(self.default_active_window_end_hour)))

    def max_continuous_runtime_minutes(self) -> int:
        if self.max_continuous_runtime_minutes_env_var:
            raw = str(os.getenv(self.max_continuous_runtime_minutes_env_var, str(self.default_max_continuous_runtime_minutes)) or "").strip()
            try:
                return max(0, int(raw))
            except Exception:
                return max(0, int(self.default_max_continuous_runtime_minutes))
        return max(0, int(self.default_max_continuous_runtime_minutes))

    def dormant_after_zero_scans(self) -> int:
        if self.dormant_after_zero_scans_env_var:
            raw = str(os.getenv(self.dormant_after_zero_scans_env_var, str(self.default_dormant_after_zero_scans)) or "").strip()
            try:
                return max(0, int(raw))
            except Exception:
                return max(0, int(self.default_dormant_after_zero_scans))
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
        return normalized_status not in {str(item).strip().upper() for item in self.materialize_blocked_statuses}

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
                    active_window_start_hour=start_hour,
                    active_window_end_hour=end_hour,
                    max_continuous_runtime_minutes=max_minutes,
                    current_local_hour=current_hour,
                    active_since=active_since,
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


FALLBACK_WORKFLOW_SPECS: dict[str, WorkflowSpec] = {
    crewai_role_registry.WORKFLOW_FEATURE_IMPROVEMENT: WorkflowSpec(
        workflow_id=crewai_role_registry.WORKFLOW_FEATURE_IMPROVEMENT,
        lane="feature",
        display_name_zh="功能改进流程",
        description="Discover feature opportunities, wait for proposal approval, then deliver approved work items.",
        enabled=True,
        stages=(
            crewai_role_registry.STAGE_PLANNING,
            crewai_role_registry.STAGE_PROPOSAL_CONFIRMATION,
            crewai_role_registry.STAGE_DELIVERY,
        ),
        task_source="proposal",
        requires_user_confirmation=True,
        materialize_requires_approval=True,
        default_version_bump="minor",
        max_candidates_env_var="TEAMOS_SELF_UPGRADE_FEATURE_MAX_CANDIDATES",
        default_max_candidates=5,
        default_active_window_start_hour=0,
        default_active_window_end_hour=24,
        default_max_continuous_runtime_minutes=0,
        cooldown_env_var="TEAMOS_SELF_UPGRADE_FEATURE_COOLDOWN_HOURS",
        default_cooldown_hours=1,
        baseline_action_default="feature_followup",
        baseline_action_by_bump=(("major", "new_baseline"), ("minor", "new_baseline")),
    ),
    crewai_role_registry.WORKFLOW_BUG_FIX: WorkflowSpec(
        workflow_id=crewai_role_registry.WORKFLOW_BUG_FIX,
        lane="bug",
        display_name_zh="缺陷修复流程",
        description="Discover actionable bugs and deliver them directly without proposal approval.",
        enabled=True,
        stages=(
            crewai_role_registry.STAGE_PLANNING,
            crewai_role_registry.STAGE_DELIVERY,
        ),
        task_source="direct_task",
        requires_user_confirmation=False,
        materialize_requires_approval=False,
        default_version_bump="patch",
        max_candidates_env_var="TEAMOS_SELF_UPGRADE_BUG_MAX_CANDIDATES",
        default_max_candidates=2,
        default_active_window_start_hour=0,
        default_active_window_end_hour=24,
        default_max_continuous_runtime_minutes=0,
        dormant_after_zero_scans_env_var="TEAMOS_SELF_UPGRADE_BUG_DORMANT_AFTER_ZERO_SCANS",
        default_dormant_after_zero_scans=3,
        default_cooldown_hours=0,
        baseline_action_default="patch_release",
    ),
    crewai_role_registry.WORKFLOW_QUALITY_IMPROVEMENT: WorkflowSpec(
        workflow_id=crewai_role_registry.WORKFLOW_QUALITY_IMPROVEMENT,
        lane="quality",
        display_name_zh="质量改进流程",
        description="Discover code-quality improvements, confirm proposals, then deliver approved tasks.",
        enabled=True,
        stages=(
            crewai_role_registry.STAGE_PLANNING,
            crewai_role_registry.STAGE_PROPOSAL_CONFIRMATION,
            crewai_role_registry.STAGE_DELIVERY,
        ),
        task_source="proposal",
        requires_user_confirmation=True,
        materialize_requires_approval=True,
        default_version_bump="none",
        default_max_candidates=0,
        default_active_window_start_hour=0,
        default_active_window_end_hour=24,
        default_max_continuous_runtime_minutes=0,
        cooldown_env_var="TEAMOS_SELF_UPGRADE_QUALITY_COOLDOWN_HOURS",
        default_cooldown_hours=1,
        baseline_action_default="quality_improvement",
    ),
    crewai_role_registry.WORKFLOW_PROCESS_IMPROVEMENT: WorkflowSpec(
        workflow_id=crewai_role_registry.WORKFLOW_PROCESS_IMPROVEMENT,
        lane="process",
        display_name_zh="流程改进流程",
        description="Discover runtime and delivery process improvements, then deliver them with governance and cooldown controls.",
        enabled=True,
        stages=(
            crewai_role_registry.STAGE_PLANNING,
            crewai_role_registry.STAGE_DELIVERY,
        ),
        task_source="proposal",
        requires_user_confirmation=False,
        materialize_requires_approval=False,
        materialize_blocked_statuses=("REJECTED", "HOLD", "MATERIALIZED"),
        default_version_bump="none",
        default_max_candidates=0,
        default_active_window_start_hour=0,
        default_active_window_end_hour=24,
        default_max_continuous_runtime_minutes=0,
        cooldown_env_var="TEAMOS_SELF_UPGRADE_PROCESS_COOLDOWN_HOURS",
        default_cooldown_hours=24,
        baseline_action_default="process_improvement",
    ),
}


def _workflow_spec_from_doc(doc: dict[str, Any]) -> WorkflowSpec:
    baseline_raw = doc.get("baseline_action_by_bump") or {}
    baseline_items: list[tuple[str, str]] = []
    if isinstance(baseline_raw, dict):
        for key, value in baseline_raw.items():
            baseline_items.append((str(key).strip(), str(value).strip()))
    return WorkflowSpec(
        workflow_id=str(doc.get("workflow_id") or "").strip(),
        lane=str(doc.get("lane") or "").strip().lower(),
        display_name_zh=str(doc.get("display_name_zh") or "").strip(),
        description=str(doc.get("description") or "").strip(),
        enabled=bool(doc.get("enabled", True)),
        disabled_reason=str(doc.get("disabled_reason") or "").strip(),
        stages=tuple(str(item).strip() for item in list(doc.get("stages") or []) if str(item).strip()),
        task_source=str(doc.get("task_source") or "proposal").strip(),
        requires_user_confirmation=bool(doc.get("requires_user_confirmation")),
        materialize_requires_approval=bool(doc.get("materialize_requires_approval")),
        materialize_blocked_statuses=tuple(str(item).strip() for item in list(doc.get("materialize_blocked_statuses") or []) if str(item).strip()) or ("REJECTED", "HOLD", "MATERIALIZED"),
        default_version_bump=str(doc.get("default_version_bump") or "none").strip(),
        max_candidates_env_var=str(doc.get("max_candidates_env_var") or "").strip(),
        default_max_candidates=max(0, int(doc.get("default_max_candidates") or 0)),
        active_window_start_hour_env_var=str(doc.get("active_window_start_hour_env_var") or "").strip(),
        default_active_window_start_hour=min(23, max(0, int(doc.get("default_active_window_start_hour") or 0))),
        active_window_end_hour_env_var=str(doc.get("active_window_end_hour_env_var") or "").strip(),
        default_active_window_end_hour=min(24, max(0, int(doc.get("default_active_window_end_hour") or 24))),
        max_continuous_runtime_minutes_env_var=str(doc.get("max_continuous_runtime_minutes_env_var") or "").strip(),
        default_max_continuous_runtime_minutes=max(0, int(doc.get("default_max_continuous_runtime_minutes") or 0)),
        dormant_after_zero_scans_env_var=str(doc.get("dormant_after_zero_scans_env_var") or "").strip(),
        default_dormant_after_zero_scans=max(0, int(doc.get("default_dormant_after_zero_scans") or 0)),
        cooldown_env_var=str(doc.get("cooldown_env_var") or "").strip(),
        default_cooldown_hours=max(0, int(doc.get("default_cooldown_hours") or 0)),
        baseline_action_default=str(doc.get("baseline_action_default") or "").strip(),
        baseline_action_by_bump=tuple(baseline_items),
    )


def _team_workflow_override(team_id: str, workflow_id: str) -> dict[str, Any]:
    team = crewai_spec_loader.team_doc(team_id)
    raw = team.get("workflow_settings") or {}
    if not isinstance(raw, dict):
        return {}
    override = raw.get(str(workflow_id or "").strip()) or {}
    return override if isinstance(override, dict) else {}


def _project_workflow_override(project_id: str, workflow_id: str) -> dict[str, Any]:
    config = project_config_store.load_project_config(str(project_id or "").strip() or "teamos")
    repo_improvement = config.get("repo_improvement") or {}
    if not isinstance(repo_improvement, dict):
        return {}
    raw = repo_improvement.get("workflow_settings") or {}
    if not isinstance(raw, dict):
        return {}
    override = raw.get(str(workflow_id or "").strip()) or {}
    return override if isinstance(override, dict) else {}


def _apply_team_workflow_overrides(spec: WorkflowSpec, *, team_id: str, project_id: str = "teamos") -> WorkflowSpec:
    team = crewai_spec_loader.team_doc(team_id)
    workflow_ids = {str(item).strip() for item in list(team.get("workflow_ids") or []) if str(item).strip()}
    enabled = bool(spec.enabled)
    disabled_reason = str(spec.disabled_reason or "").strip()
    if workflow_ids and spec.workflow_id not in workflow_ids:
        enabled = False
        if not disabled_reason:
            disabled_reason = "workflow_not_in_team_workflow_ids"

    override = _team_workflow_override(team_id, spec.workflow_id)
    if "enabled" in override:
        enabled = bool(override.get("enabled"))
        if not enabled and not disabled_reason:
            disabled_reason = str(override.get("disabled_reason") or "").strip() or "workflow_disabled_by_team_config"
        if enabled:
            disabled_reason = ""
    max_candidates = int(spec.default_max_candidates or 0)
    active_window_start_hour = int(spec.default_active_window_start_hour or 0)
    active_window_end_hour = int(spec.default_active_window_end_hour or 24)
    max_continuous_runtime_minutes = int(spec.default_max_continuous_runtime_minutes or 0)
    dormant_after_zero_scans = int(spec.default_dormant_after_zero_scans or 0)
    if "max_candidates" in override:
        try:
            max_candidates = max(0, int(override.get("max_candidates") or 0))
        except Exception:
            max_candidates = int(spec.default_max_candidates or 0)
    if "active_window_start_hour" in override:
        try:
            active_window_start_hour = min(23, max(0, int(override.get("active_window_start_hour") or 0)))
        except Exception:
            active_window_start_hour = int(spec.default_active_window_start_hour or 0)
    if "active_window_end_hour" in override:
        try:
            active_window_end_hour = min(24, max(0, int(override.get("active_window_end_hour") or 24)))
        except Exception:
            active_window_end_hour = int(spec.default_active_window_end_hour or 24)
    if "max_continuous_runtime_minutes" in override:
        try:
            max_continuous_runtime_minutes = max(0, int(override.get("max_continuous_runtime_minutes") or 0))
        except Exception:
            max_continuous_runtime_minutes = int(spec.default_max_continuous_runtime_minutes or 0)
    if "dormant_after_zero_scans" in override:
        try:
            dormant_after_zero_scans = max(0, int(override.get("dormant_after_zero_scans") or 0))
        except Exception:
            dormant_after_zero_scans = int(spec.default_dormant_after_zero_scans or 0)
    elif not enabled and not disabled_reason:
        disabled_reason = "workflow_disabled"

    override = _project_workflow_override(project_id, spec.workflow_id)
    if "enabled" in override:
        enabled = bool(override.get("enabled"))
        if not enabled:
            disabled_reason = str(override.get("disabled_reason") or "").strip() or "workflow_disabled_by_project_config"
        else:
            disabled_reason = ""
    if "max_candidates" in override:
        try:
            max_candidates = max(0, int(override.get("max_candidates") or 0))
        except Exception:
            pass
    if "active_window_start_hour" in override:
        try:
            active_window_start_hour = min(23, max(0, int(override.get("active_window_start_hour") or 0)))
        except Exception:
            pass
    if "active_window_end_hour" in override:
        try:
            active_window_end_hour = min(24, max(0, int(override.get("active_window_end_hour") or 24)))
        except Exception:
            pass
    if "max_continuous_runtime_minutes" in override:
        try:
            max_continuous_runtime_minutes = max(0, int(override.get("max_continuous_runtime_minutes") or 0))
        except Exception:
            pass
    if "dormant_after_zero_scans" in override:
        try:
            dormant_after_zero_scans = max(0, int(override.get("dormant_after_zero_scans") or 0))
        except Exception:
            pass

    return replace(
        spec,
        enabled=enabled,
        disabled_reason=disabled_reason,
        default_max_candidates=max_candidates,
        default_active_window_start_hour=active_window_start_hour,
        default_active_window_end_hour=active_window_end_hour,
        default_max_continuous_runtime_minutes=max_continuous_runtime_minutes,
        default_dormant_after_zero_scans=dormant_after_zero_scans,
    )


def _workflow_states(target_id: str) -> dict[str, Any]:
    state = improvement_store.load_target_state(str(target_id or "").strip())
    raw = state.get("workflow_states")
    return dict(raw) if isinstance(raw, dict) else {}


def workflow_runtime_state(target_id: str, workflow_id: str) -> dict[str, Any]:
    state = _workflow_states(target_id)
    workflow_state = state.get(str(workflow_id or "").strip())
    return dict(workflow_state) if isinstance(workflow_state, dict) else {}


def update_workflow_runtime_state(target_id: str, workflow_id: str, policy: WorkflowRunPolicy) -> dict[str, Any]:
    target_key = str(target_id or "").strip()
    workflow_key = str(workflow_id or "").strip()
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


def workflow_spec(workflow_id: str, *, project_id: str = "teamos") -> WorkflowSpec:
    wid = str(workflow_id or "").strip()
    if not wid:
        raise KeyError("workflow_id is required")
    loaded = crewai_spec_loader.team_workflow_doc(crewai_role_registry.TEAM_REPO_IMPROVEMENT, wid)
    if loaded:
        return _apply_team_workflow_overrides(
            _workflow_spec_from_doc(loaded),
            team_id=crewai_role_registry.TEAM_REPO_IMPROVEMENT,
            project_id=project_id,
        )
    if wid in FALLBACK_WORKFLOW_SPECS:
        return _apply_team_workflow_overrides(
            FALLBACK_WORKFLOW_SPECS[wid],
            team_id=crewai_role_registry.TEAM_REPO_IMPROVEMENT,
            project_id=project_id,
        )
    raise KeyError(f"unknown workflow spec: {wid}")


def workflow_for_lane(lane: str, *, project_id: str = "teamos") -> WorkflowSpec:
    normalized_lane = str(lane or "bug").strip().lower() or "bug"
    for doc in crewai_spec_loader.list_team_workflow_docs(crewai_role_registry.TEAM_REPO_IMPROVEMENT):
        if str(doc.get("lane") or "").strip().lower() == normalized_lane:
            return _apply_team_workflow_overrides(
                _workflow_spec_from_doc(doc),
                team_id=crewai_role_registry.TEAM_REPO_IMPROVEMENT,
                project_id=project_id,
            )
    fallback_map = {
        "feature": crewai_role_registry.WORKFLOW_FEATURE_IMPROVEMENT,
        "bug": crewai_role_registry.WORKFLOW_BUG_FIX,
        "quality": crewai_role_registry.WORKFLOW_QUALITY_IMPROVEMENT,
        "process": crewai_role_registry.WORKFLOW_PROCESS_IMPROVEMENT,
    }
    return workflow_spec(fallback_map.get(normalized_lane, crewai_role_registry.WORKFLOW_BUG_FIX), project_id=project_id)
