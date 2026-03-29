from __future__ import annotations

from typing import Any
from uuid import uuid4

from openteam_common import utc_now_iso

from app import workspace_store

from . import review_gate, store
from .models import DeliveryRequest


class DeliveryStudioStageError(ValueError):
    pass


class DeliveryStudioInputError(ValueError):
    pass


def _request_id() -> str:
    return f"REQ-{uuid4().hex[:8].upper()}"


def create_request(*, project_id: str, title: str, text: str, created_by: str) -> dict[str, object]:
    workspace_store.ensure_project_scaffold(project_id)
    request_id = _request_id()
    artifact_dir = workspace_store.delivery_request_artifacts_dir(project_id, request_id)
    raw_record = artifact_dir / "00_intake" / "raw_requirement.md"
    raw_record.parent.mkdir(parents=True, exist_ok=True)
    raw_record.write_text(f"# Raw Requirement\n\n{text}\n", encoding="utf-8")

    req = DeliveryRequest(
        request_id=request_id,
        project_id=project_id,
        title=title,
        text=text,
        artifacts={"raw_record": str(raw_record)},
    )
    _ = created_by
    request_path = store.save_request(project_id, request_id, req.model_dump())
    out = req.model_dump()
    out["request_path"] = str(request_path)
    return out


def _require_request_stage(*, doc: dict[str, object], expected_stage: str, action: str) -> None:
    current_stage = str(doc.get("stage") or "").strip()
    if current_stage != expected_stage:
        raise DeliveryStudioStageError(f"{action} requires stage '{expected_stage}' but request is '{current_stage or 'unknown'}'")


def _require_nonblank_text(*, value: str, field_name: str, action: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DeliveryStudioInputError(f"{action} requires non-empty {field_name}")
    return text


def mark_awaiting_approval(*, project_id: str, request_id: str, final_proposal: str) -> dict[str, object]:
    doc = store.load_request(project_id, request_id)
    _require_request_stage(doc=doc, expected_stage="Discussing", action="mark_awaiting_approval")
    final_proposal = _require_nonblank_text(value=final_proposal, field_name="final_proposal", action="mark_awaiting_approval")
    doc["stage"] = "Awaiting Approval"
    doc["needs_you"] = True
    artifact_dir = workspace_store.delivery_request_artifacts_dir(project_id, request_id)
    draft = artifact_dir / "03_design" / "approval_draft.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(final_proposal + "\n", encoding="utf-8")
    doc.setdefault("artifacts", {})["approval_draft"] = str(draft)
    store.save_request(project_id, request_id, doc)
    return doc


def approve_request(*, project_id: str, request_id: str, approved_by: str, selected_option: str) -> dict[str, object]:
    doc = store.load_request(project_id, request_id)
    _require_request_stage(doc=doc, expected_stage="Awaiting Approval", action="approve_request")
    doc["stage"] = "Locked"
    doc["needs_you"] = False
    doc["spec_approved"] = True
    doc["selected_option"] = selected_option
    artifact_dir = workspace_store.delivery_request_artifacts_dir(project_id, request_id)
    approval = artifact_dir / "03_design" / "approval_record.md"
    approval.parent.mkdir(parents=True, exist_ok=True)
    approval.write_text(
        (
            "# Approval Record\n\n"
            f"- approved_by: {approved_by}\n"
            f"- selected_option: {selected_option}\n"
            f"- approved_at: {utc_now_iso()}\n"
        ),
        encoding="utf-8",
    )
    doc.setdefault("artifacts", {})["approval_record"] = str(approval)
    store.save_request(project_id, request_id, doc)
    return doc


def create_change_request(*, project_id: str, parent_request_id: str, text: str, created_by: str) -> dict[str, object]:
    child = create_request(
        project_id=project_id,
        title=f"Change for {parent_request_id}",
        text=text,
        created_by=created_by,
    )
    child["change_request_of"] = parent_request_id
    store.save_request(project_id, str(child["request_id"]), child)
    return child


def finalize_review(*, project_id: str, request_id: str, reviewer_outputs: list[dict[str, Any]]) -> dict[str, object]:
    doc = store.load_request(project_id, request_id)
    gate = review_gate.evaluate_review_gate(reviewer_outputs=reviewer_outputs)
    doc["review_gate"] = gate["review_gate"]
    doc["blocked_reason"] = gate["blocked_reason"]
    doc["stage"] = "Changes Requested" if gate["review_gate"] == "Blocked" else "CI Running"
    doc["blocked"] = gate["review_gate"] == "Blocked"
    doc["rework_ticket"] = gate["rework_ticket"]
    store.save_request(project_id, request_id, doc)
    return doc
