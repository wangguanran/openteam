from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Type

from pydantic import BaseModel

from app import crewai_spec_loader
from app.crewai_task_models import (
    DeliveryAuditResult,
    DeliveryDocumentationResult,
    DeliveryImplementationResult,
    DeliveryQAResult,
    DeliveryReviewResult,
)


@dataclass(frozen=True)
class CrewTaskSpec:
    task_name: str
    expected_output: str
    description_template: str
    output_model: Type[BaseModel]

    def render_description(self, *, payload: str) -> str:
        return self.description_template.format(payload=payload)


TASK_OUTPUT_MODEL_MAP: dict[str, Type[BaseModel]] = {
    "DeliveryImplementationResult": DeliveryImplementationResult,
    "DeliveryReviewResult": DeliveryReviewResult,
    "DeliveryQAResult": DeliveryQAResult,
    "DeliveryAuditResult": DeliveryAuditResult,
    "DeliveryDocumentationResult": DeliveryDocumentationResult,
}


FALLBACK_TASK_SPECS: dict[str, CrewTaskSpec] = {}


FALLBACK_TASK_SPECS["implement_self_upgrade_task"] = CrewTaskSpec(
    task_name="implement_self_upgrade_task",
    expected_output="A structured JSON summary of the implementation attempt.",
    description_template=(
        "Implement the task directly in the repository using the provided tools.\n"
        "Rules:\n"
        "- Modify only files under allowed_paths.\n"
        "- If allowed_paths is empty, report the blocker instead of editing random files.\n"
        "- Keep commits/task history issue-scoped.\n"
        "- Run relevant validation commands before you finish.\n"
        "- Put the exact verification commands in tests_to_run so the runtime can capture durable evidence.\n"
        "- If a blocker remains, report it in unresolved.\n\n"
        "Task context:\n{payload}"
    ),
    output_model=DeliveryImplementationResult,
)


FALLBACK_TASK_SPECS["review_self_upgrade_task"] = CrewTaskSpec(
    task_name="review_self_upgrade_task",
    expected_output="A structured JSON review decision.",
    description_template=(
        "Review the current repository diff for this self-upgrade task.\n"
        "Return a structured review decision with separate code_approved and docs_approved fields.\n"
        "Rules:\n"
        "- Review both code changes and documentation changes under allowed_paths.\n"
        "- Put code-specific blockers in code_feedback.\n"
        "- Put documentation-specific blockers in docs_feedback.\n"
        "- If documentation_policy.required is false, set docs_approved=true.\n"
        "- Set approved=true only when both code_approved and docs_approved are true.\n"
        "- Use validation_evidence from the task context. Reject if the evidence is missing, stale, or failing.\n"
        "- Reject if changed files leave allowed_paths, the task is inconsistent with the issue, or validation/test coverage is weak.\n\n"
        "Task context:\n{payload}"
    ),
    output_model=DeliveryReviewResult,
)


FALLBACK_TASK_SPECS["qa_self_upgrade_task"] = CrewTaskSpec(
    task_name="qa_self_upgrade_task",
    expected_output="A structured JSON QA decision.",
    description_template=(
        "Act as the QA gate for this task.\n"
        "Review the prior validation_evidence and use the validation tool to rerun the declared tests when needed.\n"
        "Approve only if the commands pass and the acceptance criteria are covered.\n\n"
        "Task context:\n{payload}"
    ),
    output_model=DeliveryQAResult,
)


FALLBACK_TASK_SPECS["audit_self_upgrade_issue"] = CrewTaskSpec(
    task_name="audit_self_upgrade_issue",
    expected_output="A structured JSON audit decision.",
    description_template=(
        "Audit this self-upgrade execution issue before scheduling.\n"
        "Rules:\n"
        "- Confirm whether the issue is really a bug, feature, quality, or process item.\n"
        "- Confirm whether the issue description is closed-loop enough to execute now.\n"
        "- If the issue is vague, duplicated, misclassified, or not worth doing, reject it.\n"
        "- If current_lane == bug, do not approve unless the issue/task contains explicit reproduction_steps, repo-relative test_case_files, executable reproduction_commands, and post-fix verification_steps.\n"
        "- For bug items, reproduction_commands must be real test commands/scripts that reproduce the current bug before coding. Do not invent missing files, commands, or steps.\n"
        "- For bug items, verification_commands should describe the commands QA should rerun after the fix when they are known.\n"
        "- Set closure to one of: ready, needs_clarification, split_required, duplicate, misclassified, rejected.\n"
        "- docs_required should be true when README/runbook/changelog/operator docs need to be updated.\n"
        "- Keep summary and feedback in 简体中文.\n\n"
        "Task context:\n{payload}"
    ),
    output_model=DeliveryAuditResult,
)


FALLBACK_TASK_SPECS["document_self_upgrade_task"] = CrewTaskSpec(
    task_name="document_self_upgrade_task",
    expected_output="A structured JSON documentation decision.",
    description_template=(
        "Update documentation for this self-upgrade task.\n"
        "Rules:\n"
        "- Edit only documentation paths listed in documentation_policy.allowed_paths.\n"
        "- Keep user-facing natural language in 简体中文.\n"
        "- Use validation_evidence from the task context when documenting verification; never invent test results.\n"
        "- If no documentation change is required, set approved=true and updated=false with a clear summary.\n"
        "- If documentation is required but you cannot complete it, set approved=false and explain why.\n\n"
        "Task context:\n{payload}"
    ),
    output_model=DeliveryDocumentationResult,
)


def _task_spec_from_doc(doc: dict[str, Any]) -> CrewTaskSpec:
    model_name = str(doc.get("output_model") or "").strip()
    model_cls = TASK_OUTPUT_MODEL_MAP.get(model_name)
    if model_cls is None:
        raise KeyError(f"unknown task output model: {model_name}")
    return CrewTaskSpec(
        task_name=str(doc.get("task_name") or "").strip(),
        expected_output=str(doc.get("expected_output") or "").strip(),
        description_template=str(doc.get("description_template") or "").strip(),
        output_model=model_cls,
    )


def get_task_spec(task_name: str) -> CrewTaskSpec:
    name = str(task_name or "").strip()
    loaded = crewai_spec_loader.task_doc(name)
    if loaded:
        return _task_spec_from_doc(loaded)
    if name in FALLBACK_TASK_SPECS:
        return FALLBACK_TASK_SPECS[name]
    raise KeyError(f"unknown task spec: {name}")


DELIVERY_CODING_TASK_SPEC = get_task_spec("implement_self_upgrade_task")
DELIVERY_REVIEW_TASK_SPEC = get_task_spec("review_self_upgrade_task")
DELIVERY_QA_TASK_SPEC = get_task_spec("qa_self_upgrade_task")
DELIVERY_AUDIT_TASK_SPEC = get_task_spec("audit_self_upgrade_issue")
DELIVERY_DOCUMENTATION_TASK_SPEC = get_task_spec("document_self_upgrade_task")


def kickoff_registered_task(*, kickoff_fn: Any, agent: Any, spec: CrewTaskSpec, payload: str, verbose: bool) -> BaseModel:
    return kickoff_fn(
        agent=agent,
        name=spec.task_name,
        description=spec.render_description(payload=payload),
        expected_output=spec.expected_output,
        model_cls=spec.output_model,
        verbose=verbose,
    )
