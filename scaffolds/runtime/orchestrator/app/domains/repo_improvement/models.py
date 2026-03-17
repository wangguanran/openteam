from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class DeliveryImplementationResult(BaseModel):
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    tests_to_run: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class DeliveryReviewResult(BaseModel):
    approved: bool = False
    code_approved: Optional[bool] = None
    docs_approved: Optional[bool] = None
    summary: str = ""
    feedback: list[str] = Field(default_factory=list)
    code_feedback: list[str] = Field(default_factory=list)
    docs_feedback: list[str] = Field(default_factory=list)


class DeliveryQAResult(BaseModel):
    approved: bool = False
    summary: str = ""
    commands: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)


class DeliveryAuditResult(BaseModel):
    approved: bool = False
    classification: str = "bug"
    closure: str = "pending"
    worth_doing: bool = True
    docs_required: bool = False
    module: str = ""
    summary: str = ""
    feedback: list[str] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    test_case_files: list[str] = Field(default_factory=list)
    reproduction_commands: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    reproduced_in_audit: bool = False
    reproduction_evidence: list[dict[str, Any]] = Field(default_factory=list)


class DeliveryBugReproResult(BaseModel):
    approved: bool = False
    reproduced: bool = False
    summary: str = ""
    feedback: list[str] = Field(default_factory=list)
    reproduction_commands: list[str] = Field(default_factory=list)
    reproduction_evidence: list[dict[str, Any]] = Field(default_factory=list)


class DeliveryBugTestCaseResult(BaseModel):
    approved: bool = False
    summary: str = ""
    feedback: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    test_case_files: list[str] = Field(default_factory=list)
    reproduction_commands: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)


class DeliveryDocumentationResult(BaseModel):
    approved: bool = False
    updated: bool = False
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)


class UpgradeWorkItem(BaseModel):
    title: str
    summary: str = ""
    owner_role: str = "Coding-Agent"
    review_role: str = "Review-Agent"
    qa_role: str = "QA-Agent"
    workstream_id: str = "general"
    allowed_paths: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    test_case_files: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
    test_gap_type: str = ""
    target_paths: list[str] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)
    suggested_test_files: list[str] = Field(default_factory=list)
    why_not_covered: str = ""
    worktree_hint: str = ""
    module: str = ""


class UpgradeFinding(BaseModel):
    kind: str
    lane: str = "bug"
    title: str
    summary: str
    module: str = ""
    rationale: str = ""
    impact: str = "MED"
    workstream_id: str = "general"
    files: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    test_gap_type: str = ""
    target_paths: list[str] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)
    suggested_test_files: list[str] = Field(default_factory=list)
    why_not_covered: str = ""
    version_bump: str = "patch"
    target_version: str = ""
    baseline_action: str = ""
    requires_user_confirmation: bool = False
    cooldown_hours: int = 0
    work_items: list[UpgradeWorkItem] = Field(default_factory=list)


class UpgradePlan(BaseModel):
    summary: str
    findings: list[UpgradeFinding] = Field(default_factory=list)
    ci_actions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    current_version: str = ""
    planned_version: str = ""


class StructuredBugCandidate(BaseModel):
    title: str
    summary: str = ""
    rationale: str = ""
    impact: str = "MED"
    module: str = ""
    files: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    test_case_files: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)


class StructuredBugScanResult(BaseModel):
    summary: str = ""
    findings: list[StructuredBugCandidate] = Field(default_factory=list)
    ci_actions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProposalDiscussionResponse(BaseModel):
    reply_body: str
    action: str = "pending"
    title: str = ""
    summary: str = ""
    version_bump: str = ""
    module: str = ""
