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
