from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


OUTCOME_FEASIBLE = "FEASIBLE"
OUTCOME_PARTIAL = "PARTIALLY_FEASIBLE"
OUTCOME_NOT_FEASIBLE = "NOT_FEASIBLE"
OUTCOME_NEEDS_INFO = "NEEDS_INFO"


@dataclass(frozen=True)
class Feasibility:
    outcome: str
    blockers: list[str]
    dependencies: list[str]
    risks: list[str]
    alternatives: list[str]
    needs_decision: list[str]
    suggested_plan: list[str]
    evidence: list[str]


_MISSING_INFO_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("placeholder_angle", re.compile(r"<\s*\.{2,}\s*>")),
    ("placeholder_brace", re.compile(r"\{\{\s*[^}]+\s*\}\}")),
    ("tbd", re.compile(r"\bTBD\b", re.IGNORECASE)),
    ("todo", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("question_marks", re.compile(r"\?{3,}")),
    ("cn_missing", re.compile(r"(待定|待补充|不确定|先不管|之后再说)")),
]

_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("secrets_in_git_en", re.compile(r"commit\s+.*\b(token|password|api\s*key|secret)\b", re.IGNORECASE), "Policy forbids committing secrets into git."),
    ("secrets_in_git_cn", re.compile(r"(提交|入库|写入).*(token|密钥|密码|api\s*key|secret)", re.IGNORECASE), "Policy forbids storing secrets into git."),
    ("project_into_openteam_cn", re.compile(r"(把|将).*(项目|project).*(写入|放到).*(team-?os|Team\s*OS)", re.IGNORECASE), "Repo/workspace isolation forbids writing project truth sources into the openteam repo."),
]

_HIGH_RISK_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("rm_rf", re.compile(r"\brm\s+-rf\b"), "Data deletion (rm -rf) is high risk and requires approvals."),
    ("force_push", re.compile(r"\bpush\s+--force(?:-with-lease)?\b", re.IGNORECASE), "Force push is high risk and requires approvals."),
    ("public_port", re.compile(r"(0\.0\.0\.0|公网|public\s+port|expose\s+to\s+internet)", re.IGNORECASE), "Exposing public ports is high risk and requires approvals."),
    ("system_config", re.compile(r"(sshd|防火墙|firewall|sysctl|kernel\s+param)", re.IGNORECASE), "System config changes are high risk and require approvals."),
    ("prod_deploy", re.compile(r"(生产|线上|prod|production|deploy|发布|回滚|rollback)", re.IGNORECASE), "Production deploy/rollback/migration is high risk and requires approvals."),
    ("github_repo_create_delete", re.compile(r"(create\s+repo|delete\s+repo|创建\s*repo|删除\s*repo|创建\s*仓库|删除\s*仓库)", re.IGNORECASE), "Creating/deleting GitHub repos is high risk and requires approvals."),
]

_DEPENDENCY_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("postgres", re.compile(r"\b(postgres|postgresql)\b", re.IGNORECASE), "PostgreSQL (OPENTEAM_DB_URL / psycopg)"),
    ("redis", re.compile(r"\bredis\b", re.IGNORECASE), "Redis (optional)"),
    ("github", re.compile(r"\bgithub\b|gh\s+cli|projects\s+v2", re.IGNORECASE), "GitHub API/Projects (gh auth required)"),
    ("docker", re.compile(r"\bdocker\b", re.IGNORECASE), "Docker runtime"),
    ("oauth", re.compile(r"\boauth\b|codex\s+login|device\s+auth", re.IGNORECASE), "Codex OAuth (codex login)"),
]


def _sha256_text(text: str) -> str:
    return sha256((text or "").encode("utf-8")).hexdigest()


def assess(*, scope: str, text: str) -> Feasibility:
    """
    Deterministic feasibility assessment.
    - No LLM.
    - Purely rule/regex based.
    """
    s = str(text or "")
    lowered = s.lower()
    scope_s = str(scope or "").strip()

    blockers: list[str] = []
    dependencies: list[str] = []
    risks: list[str] = []
    alternatives: list[str] = []
    needs_decision: list[str] = []
    suggested_plan: list[str] = []
    evidence: list[str] = []

    # Missing-info detector.
    missing_hits = []
    for name, pat in _MISSING_INFO_PATTERNS:
        if pat.search(s):
            missing_hits.append(name)
    if missing_hits:
        blockers.append("Missing required details (placeholders present).")
        evidence.append("missing_info_hits=" + ",".join(sorted(set(missing_hits))))
        needs_decision.append("Provide missing details and confirm acceptance criteria.")

    # Forbidden policy detector.
    forbidden_hits = []
    for name, pat, msg in _FORBIDDEN_PATTERNS:
        if pat.search(s):
            forbidden_hits.append(name)
            blockers.append(msg)
    if forbidden_hits:
        evidence.append("forbidden_hits=" + ",".join(sorted(set(forbidden_hits))))
        alternatives.append("Rewrite the requirement to comply with governance policies (no secrets in git; workspace separation).")

    # High-risk detector (approvals required).
    high_risk_hits = []
    for name, pat, msg in _HIGH_RISK_PATTERNS:
        if pat.search(s):
            high_risk_hits.append(name)
            risks.append(msg)
    if high_risk_hits:
        evidence.append("high_risk_hits=" + ",".join(sorted(set(high_risk_hits))))
        dependencies.append("Approvals engine (DB-backed) must approve before execution.")

    # Dependencies detector.
    dep_hits = []
    for name, pat, dep in _DEPENDENCY_PATTERNS:
        if pat.search(s):
            dep_hits.append(name)
            dependencies.append(dep)
    if dep_hits:
        evidence.append("dependency_hits=" + ",".join(sorted(set(dep_hits))))

    # Suggested deterministic plan scaffold.
    suggested_plan.append("Capture raw input (append-only) and generate feasibility report.")
    suggested_plan.append("If NEEDS_INFO/NOT_FEASIBLE: create NEED_PM_DECISION item with report link; stop expansion.")
    suggested_plan.append("If FEASIBLE/PARTIALLY_FEASIBLE: run drift/conflict checks; update requirements.yaml + REQUIREMENTS.md + CHANGELOG.md.")
    suggested_plan.append("Trigger prompt compile / projects sync (gated) for the same scope.")

    # Outcome decision.
    outcome = OUTCOME_FEASIBLE
    if forbidden_hits:
        outcome = OUTCOME_NOT_FEASIBLE
    elif missing_hits:
        outcome = OUTCOME_NEEDS_INFO

    # Partial-feasible heuristic: mixes high risk (but allowed) + significant deps (still feasible).
    if outcome == OUTCOME_FEASIBLE and high_risk_hits:
        outcome = OUTCOME_PARTIAL
        needs_decision.append("Confirm approvals policy and execution window for high-risk actions.")

    # Deterministic normalization.
    blockers = sorted(set([x.strip() for x in blockers if str(x).strip()]))
    dependencies = sorted(set([x.strip() for x in dependencies if str(x).strip()]))
    risks = sorted(set([x.strip() for x in risks if str(x).strip()]))
    alternatives = sorted(set([x.strip() for x in alternatives if str(x).strip()]))
    needs_decision = sorted(set([x.strip() for x in needs_decision if str(x).strip()]))
    suggested_plan = [x.strip() for x in suggested_plan if str(x).strip()]
    evidence = sorted(set([x.strip() for x in evidence if str(x).strip()]))

    # Always include minimal context evidence.
    evidence.append("scope=" + scope_s)
    evidence.append("text_sha256=" + _sha256_text(s))

    return Feasibility(
        outcome=outcome,
        blockers=blockers,
        dependencies=dependencies,
        risks=risks,
        alternatives=alternatives,
        needs_decision=needs_decision,
        suggested_plan=suggested_plan,
        evidence=sorted(set(evidence)),
    )


def render_report(*, raw: dict[str, Any], assessment: Feasibility) -> str:
    raw_id = str(raw.get("raw_id") or "").strip()
    ts = str(raw.get("timestamp") or "").strip()
    scope = str(raw.get("scope") or "").strip()
    user = str(raw.get("user") or "").strip()
    channel = str(raw.get("channel") or "").strip()

    def bullets(items: list[str]) -> str:
        if not items:
            return "- (none)"
        return "\n".join([f"- {x}" for x in items]).rstrip()

    lines: list[str] = []
    lines.append("# Feasibility Report")
    lines.append("")
    lines.append("## Context")
    lines.append("")
    lines.append(f"- raw_id: {raw_id}")
    lines.append(f"- timestamp: {ts}")
    lines.append(f"- scope: {scope}")
    lines.append(f"- user: {user}")
    lines.append(f"- channel: {channel}")
    lines.append("")
    lines.append("## Outcome")
    lines.append("")
    lines.append(f"- outcome: {assessment.outcome}")
    lines.append("")
    lines.append("## Blockers")
    lines.append("")
    lines.append(bullets(assessment.blockers))
    lines.append("")
    lines.append("## Dependencies")
    lines.append("")
    lines.append(bullets(assessment.dependencies))
    lines.append("")
    lines.append("## Risks")
    lines.append("")
    lines.append(bullets(assessment.risks))
    lines.append("")
    lines.append("## Alternatives")
    lines.append("")
    lines.append(bullets(assessment.alternatives))
    lines.append("")
    lines.append("## Needs Decision / More Info")
    lines.append("")
    lines.append(bullets(assessment.needs_decision))
    lines.append("")
    lines.append("## Suggested Plan (deterministic scaffold)")
    lines.append("")
    lines.append(bullets(assessment.suggested_plan))
    lines.append("")
    lines.append("## Evidence (machine)")
    lines.append("")
    lines.append("```text")
    lines.append("\n".join(assessment.evidence).strip())
    lines.append("```")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"
