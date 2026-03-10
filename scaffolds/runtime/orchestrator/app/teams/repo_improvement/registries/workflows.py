from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from app import crewai_role_registry
from app import project_config_store
from app import crewai_spec_loader


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
        default_max_candidates=0,
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
    if "max_candidates" in override:
        try:
            max_candidates = max(0, int(override.get("max_candidates") or 0))
        except Exception:
            max_candidates = int(spec.default_max_candidates or 0)
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

    return replace(spec, enabled=enabled, disabled_reason=disabled_reason, default_max_candidates=max_candidates)


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
