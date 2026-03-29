from __future__ import annotations

from typing import Any


def evaluate_review_gate(*, reviewer_outputs: list[dict[str, Any]]) -> dict[str, Any]:
    blocked_reasons: list[str] = []
    for item in reviewer_outputs:
        if str(item.get("decision") or "").upper() == "BLOCK":
            blocked_reasons.extend([str(x) for x in (item.get("blocking_issues") or []) if str(x).strip()])
        if not bool(item.get("test_complete", False)):
            blocked_reasons.append(f"{item.get('reviewer_id', 'reviewer')}: test completeness failed")
    blocked_reasons = [reason for reason in blocked_reasons if reason]
    if blocked_reasons:
        return {
            "review_gate": "Blocked",
            "blocking_gate": "failure",
            "blocked_reason": "; ".join(blocked_reasons),
            "rework_ticket": {
                "blocking_issues": blocked_reasons,
                "acceptance_criteria": "All blocking findings resolved; tests and coverage updated; reviewers rerun.",
            },
        }
    return {
        "review_gate": "Passed",
        "blocking_gate": "success",
        "blocked_reason": "",
        "rework_ticket": None,
    }


def build_check_runs(*, request_id: str, reviewer_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gate = evaluate_review_gate(reviewer_outputs=reviewer_outputs)
    checks = []
    for item in reviewer_outputs:
        checks.append(
            {
                "name": f"panel-review/{item['reviewer_id']}",
                "status": "completed",
                "conclusion": "failure" if str(item.get("decision") or "").upper() == "BLOCK" else "success",
                "output": {"title": request_id, "summary": "; ".join(item.get("blocking_issues") or []) or "PASS"},
            }
        )
    checks.append(
        {
            "name": "panel-review/blocking-gate",
            "status": "completed",
            "conclusion": gate["blocking_gate"],
            "output": {"title": request_id, "summary": gate["blocked_reason"] or "All reviewers passed"},
        }
    )
    return checks
