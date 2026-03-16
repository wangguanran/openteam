from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app import crewai_spec_loader


ROLE_PRODUCT_MANAGER = "Product-Manager"
ROLE_TEST_MANAGER = "Test-Manager"
ROLE_ISSUE_DRAFTER = "Issue-Drafter"
ROLE_PLAN_REVIEW_AGENT = "Plan-Review-Agent"
ROLE_PLAN_QA_AGENT = "Plan-QA-Agent"
ROLE_REVIEW_AGENT = "Review-Agent"
ROLE_QA_AGENT = "QA-Agent"
ROLE_PROCESS_OPTIMIZATION_ANALYST = "Process-Optimization-Analyst"
ROLE_ISSUE_DISCUSSION_AGENT = "Issue-Discussion-Agent"
ROLE_TEST_CASE_GAP_AGENT = "Test-Case-Gap-Agent"
ROLE_ISSUE_AUDIT_AGENT = "Issue-Audit-Agent"
ROLE_BUG_REPRO_AGENT = "Bug-Repro-Agent"
ROLE_BUG_TESTCASE_AGENT = "Bug-TestCase-Agent"
ROLE_DOCUMENTATION_AGENT = "Documentation-Agent"
ROLE_MILESTONE_MANAGER = "Milestone-Manager-Agent"
ROLE_CODE_QUALITY_ANALYST = "Code-Quality-Analyst"
ROLE_FEATURE_CODING_AGENT = "Feature-Coding-Agent"
ROLE_BUGFIX_CODING_AGENT = "Bugfix-Coding-Agent"
ROLE_PROCESS_OPTIMIZATION_AGENT = "Process-Optimization-Agent"
ROLE_CODE_QUALITY_AGENT = "Code-Quality-Agent"
ROLE_SCHEDULER_AGENT = "Scheduler-Agent"
ROLE_RELEASE_AGENT = "Release-Agent"
ROLE_PROCESS_METRICS_AGENT = "Process-Metrics-Agent"

TEAM_REPO_IMPROVEMENT = "repo-improvement"
STAGE_PLANNING = "planning"
STAGE_PROPOSAL_CONFIRMATION = "proposal-confirmation"
STAGE_DELIVERY = "delivery"

WORKFLOW_BUG_FINDING = "bug-finding"
WORKFLOW_BUG_CODING = "bug-coding"
WORKFLOW_FEATURE_FINDING = "feature-finding"
WORKFLOW_FEATURE_DISCUSSION = "feature-discussion"
WORKFLOW_FEATURE_CODING = "feature-coding"
WORKFLOW_QUALITY_FINDING = "quality-finding"
WORKFLOW_QUALITY_DISCUSSION = "quality-discussion"
WORKFLOW_QUALITY_CODING = "quality-coding"
WORKFLOW_PROCESS_FINDING = "process-finding"
WORKFLOW_PROCESS_CODING = "process-coding"

WORKFLOW_FEATURE_IMPROVEMENT = "feature-improvement"
WORKFLOW_BUG_FIX = "bug-fix"
WORKFLOW_QUALITY_IMPROVEMENT = "quality-improvement"
WORKFLOW_PROCESS_IMPROVEMENT = "process-improvement"

LEGACY_WORKFLOW_ID_ALIASES = {
    WORKFLOW_FEATURE_IMPROVEMENT: WORKFLOW_FEATURE_FINDING,
    WORKFLOW_BUG_FIX: WORKFLOW_BUG_FINDING,
    WORKFLOW_QUALITY_IMPROVEMENT: WORKFLOW_QUALITY_FINDING,
    WORKFLOW_PROCESS_IMPROVEMENT: WORKFLOW_PROCESS_FINDING,
}


ROLE_DISPLAY_ZH = {
    ROLE_PRODUCT_MANAGER: "产品经理",
    ROLE_TEST_MANAGER: "测试经理",
    ROLE_ISSUE_DRAFTER: "提单 Agent",
    ROLE_PLAN_REVIEW_AGENT: "规划评审 Agent",
    ROLE_PLAN_QA_AGENT: "规划 QA Agent",
    ROLE_REVIEW_AGENT: "评审 Agent",
    ROLE_QA_AGENT: "QA Agent",
    ROLE_PROCESS_OPTIMIZATION_ANALYST: "流程优化分析 Agent",
    ROLE_ISSUE_DISCUSSION_AGENT: "需求答复 Agent",
    ROLE_TEST_CASE_GAP_AGENT: "测试缺口分析 Agent",
    ROLE_ISSUE_AUDIT_AGENT: "问题审计 Agent",
    ROLE_BUG_REPRO_AGENT: "缺陷复现 Agent",
    ROLE_BUG_TESTCASE_AGENT: "缺陷测试用例 Agent",
    ROLE_DOCUMENTATION_AGENT: "文档同步 Agent",
    ROLE_MILESTONE_MANAGER: "里程碑经理 Agent",
    ROLE_CODE_QUALITY_ANALYST: "代码质量分析 Agent",
    ROLE_FEATURE_CODING_AGENT: "功能编码 Agent",
    ROLE_BUGFIX_CODING_AGENT: "缺陷修复 Agent",
    ROLE_PROCESS_OPTIMIZATION_AGENT: "流程优化编码 Agent",
    ROLE_CODE_QUALITY_AGENT: "代码质量治理 Agent",
    ROLE_SCHEDULER_AGENT: "调度 Agent",
    ROLE_RELEASE_AGENT: "发布 Agent",
    ROLE_PROCESS_METRICS_AGENT: "流程指标 Agent",
}


@dataclass(frozen=True)
class CrewRoleSpec:
    role_id: str
    display_name_zh: str = ""
    goal: str = ""
    backstory: str = ""
    tool_profile: str = ""


@dataclass(frozen=True)
class TeamMemberSpec:
    role_id: str
    state: str
    current_action: str


@dataclass(frozen=True)
class TeamBlueprint:
    team_id: str
    members: tuple[TeamMemberSpec, ...]


ROLE_SPECS: dict[str, CrewRoleSpec] = {
    ROLE_PRODUCT_MANAGER: CrewRoleSpec(
        role_id=ROLE_PRODUCT_MANAGER,
        goal="Identify worthwhile feature improvements and product-level optimizations for the target repository.",
        backstory="You think like a product manager. You prioritize user-visible value, versioning impact, and whether a change belongs in a new baseline.",
    ),
    ROLE_TEST_MANAGER: CrewRoleSpec(
        role_id=ROLE_TEST_MANAGER,
        goal="Identify actionable bugs, regressions, and concrete defect signals from the repository context.",
        backstory="You reason like a QA/test lead and focus on reproducible defects, failing behavior, and operational risk. Pure test coverage gaps belong to the dedicated test-case gap role instead of this bug role.",
    ),
    ROLE_TEST_CASE_GAP_AGENT: CrewRoleSpec(
        role_id=ROLE_TEST_CASE_GAP_AGENT,
        goal="Identify high-value black-box and white-box test coverage gaps that should become explicit repo improvement issues.",
        backstory="You think like a test architecture lead. You map behavior paths and internal branches to existing tests, flag uncovered paths, distinguish black-box versus white-box gaps, and recommend concrete test file locations.",
    ),
    ROLE_ISSUE_DRAFTER: CrewRoleSpec(
        role_id=ROLE_ISSUE_DRAFTER,
        goal="Break features and bug fixes into small, execution-scoped engineering work items suitable for GitHub Projects and downstream coding agents.",
        backstory="You think like a delivery lead. You keep issues small, explicit, and scoped to one piece of work, with clear owner roles and worktree hints.",
    ),
    ROLE_PLAN_REVIEW_AGENT: CrewRoleSpec(
        role_id=ROLE_PLAN_REVIEW_AGENT,
        goal="Enforce code review constraints so coding agents only touch issue-scoped files and commit history remains task-linked.",
        backstory="You act like an engineering reviewer protecting scope discipline, commit hygiene, and release boundaries.",
    ),
    ROLE_PLAN_QA_AGENT: CrewRoleSpec(
        role_id=ROLE_PLAN_QA_AGENT,
        goal="Ensure each work item has explicit verification, QA handoff, and close criteria before it can be considered done.",
        backstory="You are the final delivery gate. No item closes without review and QA evidence.",
    ),
    ROLE_PROCESS_OPTIMIZATION_ANALYST: CrewRoleSpec(
        role_id=ROLE_PROCESS_OPTIMIZATION_ANALYST,
        goal="Use recent execution telemetry to identify improvements in the repo-improvement process itself.",
        backstory="You optimize the team workflow by looking at timings, failures, repeated blockers, and wasted motion.",
    ),
    ROLE_CODE_QUALITY_ANALYST: CrewRoleSpec(
        role_id=ROLE_CODE_QUALITY_ANALYST,
        goal="Identify code quality improvements grounded in repository structure, duplicated logic, large files, stale files, and weak reuse boundaries.",
        backstory="You think like a staff engineer doing code health stewardship. You look for dead code, cleanup opportunities, safer deletions, and refactors that increase reuse without changing product scope.",
    ),
    ROLE_ISSUE_DISCUSSION_AGENT: CrewRoleSpec(
        role_id=ROLE_ISSUE_DISCUSSION_AGENT,
        goal="Respond to improvement proposal questions, clarify scope, and update the proposal without starting development until the user confirms.",
        backstory="You act like the PM-side proposal discussion owner. You answer questions, tighten the proposal, and only approve development when the user is explicit.",
    ),
    ROLE_ISSUE_AUDIT_AGENT: CrewRoleSpec(
        role_id=ROLE_ISSUE_AUDIT_AGENT,
        goal="Audit the issue before scheduling any coding work, confirm the classification, and reject work that is duplicate, misclassified, stale, or not closed-loop enough to route into the delivery workflow.",
        backstory="You are the delivery audit gate. You stop vague, duplicate, misclassified, or low-value issues before the scheduler dispatches engineering work. For bug issues, you verify whether the report is worth pursuing now and route it into dedicated bug reproduction or test-case bootstrap stages instead of letting vague reports reach coding.",
        tool_profile="qa",
    ),
    ROLE_BUG_REPRO_AGENT: CrewRoleSpec(
        role_id=ROLE_BUG_REPRO_AGENT,
        goal="Use the current bug contract to prove whether the bug is still reproducible before any bugfix coding starts.",
        backstory="You are the pre-fix reproduction gate. You only trust executable evidence, you rerun the declared failing commands, and you close stale bug reports instead of sending them downstream.",
        tool_profile="qa",
    ),
    ROLE_BUG_TESTCASE_AGENT: CrewRoleSpec(
        role_id=ROLE_BUG_TESTCASE_AGENT,
        goal="Bootstrap the smallest failing automated test case that proves the reported bug exists in the current task worktree.",
        backstory="You are a bug-validation specialist. You create the minimum failing test under approved test paths, capture the exact reproduction commands, and stop if the bug cannot be turned into a stable executable test.",
        tool_profile="write",
    ),
    ROLE_FEATURE_CODING_AGENT: CrewRoleSpec(
        role_id=ROLE_FEATURE_CODING_AGENT,
        goal="Implement the approved repo-improvement task directly in the repository while staying inside the declared issue scope.",
        backstory="You are a disciplined software engineer. You only change allowed paths, you run validation before stopping, and you do not add unrelated improvements.",
        tool_profile="write",
    ),
    ROLE_BUGFIX_CODING_AGENT: CrewRoleSpec(
        role_id=ROLE_BUGFIX_CODING_AGENT,
        goal="Implement the approved repo-improvement task directly in the repository while staying inside the declared issue scope.",
        backstory="You are a disciplined software engineer. You only change allowed paths, you run validation before stopping, and you do not add unrelated improvements.",
        tool_profile="write",
    ),
    ROLE_PROCESS_OPTIMIZATION_AGENT: CrewRoleSpec(
        role_id=ROLE_PROCESS_OPTIMIZATION_AGENT,
        goal="Implement the approved repo-improvement task directly in the repository while staying inside the declared issue scope.",
        backstory="You are a disciplined software engineer. You only change allowed paths, you run validation before stopping, and you do not add unrelated improvements.",
        tool_profile="write",
    ),
    ROLE_CODE_QUALITY_AGENT: CrewRoleSpec(
        role_id=ROLE_CODE_QUALITY_AGENT,
        goal="Implement the approved repo-improvement task directly in the repository while staying inside the declared issue scope.",
        backstory="You are a disciplined software engineer. You only change allowed paths, you run validation before stopping, and you do not add unrelated improvements.",
        tool_profile="write",
    ),
    ROLE_REVIEW_AGENT: CrewRoleSpec(
        role_id=ROLE_REVIEW_AGENT,
        goal="Review the current task diff and reject anything outside scope, under-tested, or inconsistent with the task contract.",
        backstory="You are a strict code reviewer. You care about scope discipline, testability, and acceptance coverage.",
        tool_profile="read",
    ),
    ROLE_QA_AGENT: CrewRoleSpec(
        role_id=ROLE_QA_AGENT,
        goal="Run the declared validation commands and confirm the task meets its acceptance criteria before release.",
        backstory="You are the QA gate. If tests fail or acceptance is weak, you block release and send the task back.",
        tool_profile="qa",
    ),
    ROLE_DOCUMENTATION_AGENT: CrewRoleSpec(
        role_id=ROLE_DOCUMENTATION_AGENT,
        goal="Update the repository documentation, operator runbooks, and release notes required by the validated issue before release.",
        backstory="You are the documentation gate. You only edit approved documentation paths, keep language precise, and ensure the written docs match the actual delivered behavior.",
        tool_profile="write",
    ),
    ROLE_MILESTONE_MANAGER: CrewRoleSpec(
        role_id=ROLE_MILESTONE_MANAGER,
        goal="Plan release lines and milestones for approved repo-improvement work.",
        backstory="You keep milestones coherent, incremental, and traceable to the workstream plan.",
    ),
    ROLE_SCHEDULER_AGENT: CrewRoleSpec(
        role_id=ROLE_SCHEDULER_AGENT,
        goal="Coordinate task stage transitions and retries across the delivery workflow.",
        backstory="You do not author code; you keep the workflow moving and route work to the right gate.",
    ),
    ROLE_RELEASE_AGENT: CrewRoleSpec(
        role_id=ROLE_RELEASE_AGENT,
        goal="Ship validated task changes through git and GitHub release plumbing.",
        backstory="You take the validated diff, preserve branch hygiene, and fail safely when release constraints are not met.",
    ),
    ROLE_PROCESS_METRICS_AGENT: CrewRoleSpec(
        role_id=ROLE_PROCESS_METRICS_AGENT,
        goal="Record delivery telemetry and runtime execution signals.",
        backstory="You observe workflow health and write metrics; you do not author product changes.",
    ),
}


def _role_spec_from_doc(doc: dict[str, Any]) -> CrewRoleSpec:
    return CrewRoleSpec(
        role_id=str(doc.get("role_id") or "").strip(),
        display_name_zh=str(doc.get("display_name_zh") or "").strip(),
        goal=str(doc.get("goal") or "").strip(),
        backstory=str(doc.get("backstory") or "").strip(),
        tool_profile=str(doc.get("tool_profile") or "").strip(),
    )


def _resolve_template_value(raw: Any, context: dict[str, str]) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return text.format(**context)
    except Exception:
        return text


def _team_blueprint_from_doc(doc: dict[str, Any], *, context: Optional[dict[str, str]] = None) -> TeamBlueprint:
    ctx = {str(k): str(v) for k, v in dict(context or {}).items()}
    members: list[TeamMemberSpec] = []
    for raw_member in list(doc.get("members") or []):
        member = raw_member if isinstance(raw_member, dict) else {}
        role_id = _resolve_template_value(member.get("role_id"), ctx)
        state = _resolve_template_value(member.get("state"), ctx)
        current_action = _resolve_template_value(member.get("current_action"), ctx)
        if role_id and state and current_action:
            members.append(TeamMemberSpec(role_id=role_id, state=state, current_action=current_action))
    blueprint_id = str(doc.get("team_id") or doc.get("stage_id") or doc.get("workflow_id") or "").strip()
    return TeamBlueprint(team_id=blueprint_id, members=tuple(members))


def role_display_zh(role_id: str) -> str:
    rid = str(role_id or "").strip()
    loaded = crewai_spec_loader.role_doc(rid)
    label = str(loaded.get("display_name_zh") or "").strip()
    if label:
        return label
    return ROLE_DISPLAY_ZH.get(rid, rid or "未命名角色")


def get_role_spec(role_id: str, *, fallback_role_id: str = "") -> CrewRoleSpec:
    rid = str(role_id or "").strip()
    loaded = crewai_spec_loader.role_doc(rid)
    if loaded:
        return _role_spec_from_doc(loaded)
    if rid in ROLE_SPECS:
        return ROLE_SPECS[rid]
    fallback = str(fallback_role_id or "").strip()
    loaded_fallback = crewai_spec_loader.role_doc(fallback)
    if loaded_fallback:
        spec = _role_spec_from_doc(loaded_fallback)
        return CrewRoleSpec(role_id=rid or spec.role_id, display_name_zh=spec.display_name_zh, goal=spec.goal, backstory=spec.backstory, tool_profile=spec.tool_profile)
    if fallback in ROLE_SPECS:
        spec = ROLE_SPECS[fallback]
        return CrewRoleSpec(role_id=rid or spec.role_id, display_name_zh=spec.display_name_zh, goal=spec.goal, backstory=spec.backstory, tool_profile=spec.tool_profile)
    return CrewRoleSpec(role_id=rid)


def planning_team_blueprint() -> TeamBlueprint:
    loaded = crewai_spec_loader.team_stage_doc(TEAM_REPO_IMPROVEMENT, STAGE_PLANNING)
    if loaded:
        return _team_blueprint_from_doc(loaded)
    return TeamBlueprint(
        team_id=STAGE_PLANNING,
        members=(
            TeamMemberSpec(role_id=ROLE_PRODUCT_MANAGER, state="RUNNING", current_action="discovering feature opportunities"),
            TeamMemberSpec(role_id=ROLE_TEST_MANAGER, state="RUNNING", current_action="scanning bugs and regressions"),
            TeamMemberSpec(role_id=ROLE_TEST_CASE_GAP_AGENT, state="RUNNING", current_action="mapping black-box and white-box test gaps"),
            TeamMemberSpec(role_id=ROLE_ISSUE_DRAFTER, state="RUNNING", current_action="splitting work into executable items"),
            TeamMemberSpec(role_id=ROLE_PLAN_REVIEW_AGENT, state="RUNNING", current_action="checking scope and review gates"),
            TeamMemberSpec(role_id=ROLE_PLAN_QA_AGENT, state="RUNNING", current_action="reviewing QA and acceptance gates"),
            TeamMemberSpec(role_id=ROLE_PROCESS_OPTIMIZATION_ANALYST, state="RUNNING", current_action="analyzing process telemetry"),
            TeamMemberSpec(role_id=ROLE_CODE_QUALITY_ANALYST, state="RUNNING", current_action="reviewing code quality and cleanup opportunities"),
            TeamMemberSpec(role_id=ROLE_MILESTONE_MANAGER, state="RUNNING", current_action="planning release lines and milestones"),
        ),
    )


def discussion_team_blueprint() -> TeamBlueprint:
    loaded = crewai_spec_loader.team_stage_doc(TEAM_REPO_IMPROVEMENT, STAGE_PROPOSAL_CONFIRMATION)
    if loaded:
        return _team_blueprint_from_doc(loaded)
    return TeamBlueprint(
        team_id=STAGE_PROPOSAL_CONFIRMATION,
        members=(TeamMemberSpec(role_id=ROLE_ISSUE_DISCUSSION_AGENT, state="RUNNING", current_action="replying to proposal discussion"),),
    )


def delivery_team_blueprint(*, owner_role: str, review_role: str, qa_role: str, documentation_role: str) -> TeamBlueprint:
    loaded = crewai_spec_loader.team_stage_doc(TEAM_REPO_IMPROVEMENT, STAGE_DELIVERY)
    if loaded:
        return _team_blueprint_from_doc(
            loaded,
            context={
                "owner_role": str(owner_role),
                "review_role": str(review_role),
                "qa_role": str(qa_role),
                "documentation_role": str(documentation_role),
            },
        )
    return TeamBlueprint(
        team_id=STAGE_DELIVERY,
        members=(
            TeamMemberSpec(role_id=ROLE_SCHEDULER_AGENT, state="RUNNING", current_action="dispatching repo-improvement task"),
            TeamMemberSpec(role_id=ROLE_ISSUE_AUDIT_AGENT, state="IDLE", current_action="waiting for issue audit"),
            TeamMemberSpec(role_id=ROLE_BUG_TESTCASE_AGENT, state="IDLE", current_action="waiting for failing test bootstrap"),
            TeamMemberSpec(role_id=ROLE_BUG_REPRO_AGENT, state="IDLE", current_action="waiting for bug reproduction"),
            TeamMemberSpec(role_id=str(owner_role), state="IDLE", current_action="waiting for coding"),
            TeamMemberSpec(role_id=str(review_role), state="IDLE", current_action="waiting for review"),
            TeamMemberSpec(role_id=str(qa_role), state="IDLE", current_action="waiting for QA"),
            TeamMemberSpec(role_id=str(documentation_role), state="IDLE", current_action="waiting for docs sync"),
            TeamMemberSpec(role_id=ROLE_RELEASE_AGENT, state="IDLE", current_action="waiting for release"),
            TeamMemberSpec(role_id=ROLE_PROCESS_METRICS_AGENT, state="RUNNING", current_action="collecting delivery telemetry"),
        ),
    )


def register_team_blueprint(*, db: Any, blueprint: TeamBlueprint, project_id: str, workstream_id: str, task_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for member in blueprint.members:
        out[member.role_id] = db.register_agent(
            role_id=member.role_id,
            project_id=project_id,
            workstream_id=workstream_id,
            task_id=task_id,
            state=member.state,
            current_action=member.current_action,
        )
    return out


def role_ids_for_team(blueprint: TeamBlueprint) -> tuple[str, ...]:
    return tuple(member.role_id for member in blueprint.members)
