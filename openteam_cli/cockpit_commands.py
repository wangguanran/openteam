from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .http import _http_json
from .team import _resolve_team_watch_run_id


class CockpitCommandError(RuntimeError):
    pass


_DEFAULT_REVIEWERS = ("reviewer-a", "reviewer-b", "reviewer-c")


def load_request(*, base_url: str, team_id: str, request_id: str) -> dict[str, Any]:
    team = str(team_id or "").strip()
    req = str(request_id or "").strip()
    if not team:
        raise CockpitCommandError("team_id is required")
    if not req:
        raise CockpitCommandError("request_id is required")
    return _http_json(
        "GET",
        str(base_url).rstrip("/") + f"/v1/teams/{quote(team, safe='')}/requests/{quote(req, safe='')}",
    )


def _require_request_id(request_id: str, *, action: str) -> str:
    rid = str(request_id or "").strip()
    if not rid:
        raise CockpitCommandError(f"{action} requires --request-id or an active request context")
    return rid


def _default_pass_output(reviewer_id: str) -> dict[str, Any]:
    return {
        "reviewer_id": reviewer_id,
        "decision": "PASS",
        "blocking_issues": [],
        "test_complete": True,
    }


def _build_reviewer_outputs(spec: str) -> list[dict[str, Any]]:
    raw = str(spec or "").strip()
    if not raw or raw.lower() == "pass":
        return [_default_pass_output(reviewer_id) for reviewer_id in _DEFAULT_REVIEWERS]
    if raw.lower().startswith("block"):
        reason = raw[5:].strip() or "cockpit requested review block"
        outputs = [_default_pass_output(reviewer_id) for reviewer_id in _DEFAULT_REVIEWERS]
        outputs[1] = {
            "reviewer_id": "reviewer-b",
            "decision": "BLOCK",
            "blocking_issues": [reason],
            "test_complete": True,
        }
        return outputs
    if raw.lower().startswith("tests-missing"):
        detail = raw[len("tests-missing") :].strip()
        outputs = [_default_pass_output(reviewer_id) for reviewer_id in _DEFAULT_REVIEWERS]
        outputs[1] = {
            "reviewer_id": "reviewer-b",
            "decision": "PASS",
            "blocking_issues": [detail] if detail else [],
            "test_complete": False,
        }
        return outputs
    raise CockpitCommandError("unsupported /review syntax; use `pass`, `block <reason>`, or `tests-missing [reason]`")


def execute_input(
    *,
    base_url: str,
    team_id: str,
    project_id: str,
    request_id: str,
    explicit_run_id: str,
    routed: dict[str, str],
) -> dict[str, Any]:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise CockpitCommandError("base_url is required")
    team = str(team_id or "").strip() or "delivery-studio"
    project = str(project_id or "").strip()
    mode = str((routed or {}).get("mode") or "").strip()
    target = str((routed or {}).get("target") or "").strip()
    text = str((routed or {}).get("text") or "").strip()

    if mode == "command":
        if target == "propose":
            rid = _require_request_id(request_id, action="/propose")
            request = _http_json(
                "POST",
                base + f"/v1/teams/{quote(team, safe='')}/requests/{quote(rid, safe='')}/awaiting-approval",
                {"project_id": project, "final_proposal": text},
            )
            return {"kind": "request", "request": request, "message": {"category": "Decision", "text": f"已提交 proposal：{text}"}}
        if target == "approve":
            rid = _require_request_id(request_id, action="/approve")
            request = _http_json(
                "POST",
                base + f"/v1/teams/{quote(team, safe='')}/requests/{quote(rid, safe='')}/approve",
                {"project_id": project, "selected_option": text},
            )
            return {"kind": "request", "request": request, "message": {"category": "Decision", "text": f"已批准：{text}"}}
        if target == "review":
            rid = _require_request_id(request_id, action="/review")
            request = _http_json(
                "POST",
                base + f"/v1/teams/{quote(team, safe='')}/requests/{quote(rid, safe='')}/review/finalize",
                {"project_id": project, "reviewer_outputs": _build_reviewer_outputs(text)},
            )
            return {"kind": "request", "request": request, "message": {"category": "Action", "text": f"已提交 review：{text or 'pass'}"}}
        if target == "watch":
            timeout_sec = 30
            if text:
                try:
                    timeout_sec = max(1, int(text))
                except ValueError as exc:
                    raise CockpitCommandError("/watch expects an integer timeout in seconds") from exc
            run_id = _resolve_team_watch_run_id(
                base,
                team_id=team,
                project_id=project,
                explicit_run_id=str(explicit_run_id or "").strip(),
            )
            if not run_id:
                raise CockpitCommandError("No active team run found for /watch")
            return {"kind": "watch", "run_id": run_id, "timeout_sec": timeout_sec}
        raise CockpitCommandError(f"unsupported cockpit command: /{target}")

    if mode == "agent":
        body = f"@{target} {text}".strip()
        _http_json(
            "POST",
            base + "/v1/chat",
            {
                "project_id": project,
                "run_id": str(explicit_run_id or "").strip(),
                "message": body,
                "message_type": "GENERAL",
            },
        )
        return {
            "kind": "message",
            "message": {
                "actor": "you",
                "role": "User",
                "model": "",
                "stage": "",
                "category": "DirectedMessage",
                "text": body,
                "target_agent": target,
            },
        }

    if mode == "panel":
        if not text:
            return {"kind": "noop"}
        _http_json(
            "POST",
            base + "/v1/chat",
            {
                "project_id": project,
                "run_id": str(explicit_run_id or "").strip(),
                "message": text,
                "message_type": "GENERAL",
            },
        )
        return {
            "kind": "message",
            "message": {
                "actor": "you",
                "role": "User",
                "model": "",
                "stage": "",
                "category": "Discussion",
                "text": text,
            },
        }

    raise CockpitCommandError(f"unsupported cockpit input mode: {mode or 'unknown'}")
