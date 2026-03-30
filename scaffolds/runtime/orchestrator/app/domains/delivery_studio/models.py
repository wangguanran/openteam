from __future__ import annotations

from typing import Literal, Optional

from app.pydantic_compat import BaseModel, Field


REQUEST_STAGES = (
    "Discussing",
    "Awaiting Approval",
    "Locked",
    "Docs Updating",
    "Plan Ready",
    "Implementing",
    "In Review",
    "Changes Requested",
    "CI Running",
    "Ready to Merge",
    "Merged",
)


class DeliveryMessage(BaseModel):
    actor: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    model: str = ""
    stage: str = Field(..., min_length=1)
    category: Literal["Discussion", "Decision", "Action", "Alert"]
    text: str = Field(..., min_length=1)
    ts: str = Field(..., min_length=1)


class DeliveryRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    stage: str = "Discussing"
    priority: str = "P1"
    needs_you: bool = False
    blocked: bool = False
    blocked_reason: str = ""
    spec_approved: bool = False
    change_request_of: Optional[str] = None
    selected_option: str = ""
    messages: list[DeliveryMessage] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
