from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from collections.abc import Mapping


@dataclass(frozen=True)
class LeftAgent:
    agent_id: str
    role: str
    model: str
    status: str


@dataclass(frozen=True)
class CenterMessage:
    actor: str
    role: str
    model: str
    stage: str
    category: str
    text: str


@dataclass(frozen=True)
class RightStatus:
    request_id: str
    stage: str
    needs_you: bool
    blocked: bool
    review_gate: str
    ci: str
    pr: str
    workstreams: Mapping[str, str]


@dataclass(frozen=True)
class CockpitSnapshot:
    left: tuple[LeftAgent, ...]
    center: tuple[CenterMessage, ...]
    right: RightStatus


def route_input(text: str) -> dict[str, str]:
    raw = str(text or "").strip()
    if raw.startswith("@"):
        head, _, tail = raw.partition(" ")
        return {"mode": "agent", "target": head[1:], "text": tail.strip()}
    if raw.startswith("/"):
        head, _, tail = raw.partition(" ")
        return {"mode": "command", "target": head[1:], "text": tail.strip()}
    return {"mode": "panel", "target": "panel", "text": raw}


def build_snapshot(
    *,
    request: dict[str, Any],
    agents: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> CockpitSnapshot:
    left = tuple(
        LeftAgent(
            agent_id=str(item.get("agent_id") or ""),
            role=str(item.get("role") or ""),
            model=str(item.get("model") or ""),
            status=str(item.get("status") or ""),
        )
        for item in agents
    )
    center = tuple(
        CenterMessage(
            actor=str(item.get("actor") or ""),
            role=str(item.get("role") or ""),
            model=str(item.get("model") or ""),
            stage=str(item.get("stage") or ""),
            category=str(item.get("category") or ""),
            text=str(item.get("text") or ""),
        )
        for item in messages
    )
    right = RightStatus(
        request_id=str(request.get("request_id") or ""),
        stage=str(request.get("stage") or ""),
        needs_you=bool(request.get("needs_you")),
        blocked=bool(request.get("blocked")),
        review_gate=str(request.get("review_gate") or ""),
        ci=str(request.get("ci") or ""),
        pr=str(request.get("pr") or ""),
        workstreams=MappingProxyType(dict(request.get("workstreams") or {})),
    )
    return CockpitSnapshot(left=left, center=center, right=right)
