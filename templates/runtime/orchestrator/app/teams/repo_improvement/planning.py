from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import configparser
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from app import codex_llm
from app import crew_tools
from app import crewai_agent_factory
from app import crewai_role_registry
from app import crewai_runtime
from app import crewai_workflow_registry
from app import improvement_store
from app import workspace_store
from app.github_issues_bus import GitHubIssuesBusError, ensure_issue, ensure_milestone, list_issue_comments, update_issue, upsert_comment_with_marker
from app.github_projects_client import GitHubAPIError, GitHubAuthError
from app.panel_github_sync import GitHubProjectsPanelSync, PanelSyncError
from app.panel_mapping import PanelMappingError, get_project_cfg, load_mapping
from app.plan_store import upsert_runtime_milestone
from app.state_store import ledger_tasks_dir, runtime_state_root, team_os_root


class SelfUpgradeError(RuntimeError):
    pass


class UpgradeWorkItem(BaseModel):
    title: str
    summary: str = ""
    owner_role: str = "Feature-Coding-Agent"
    review_role: str = "Review-Agent"
    qa_role: str = "QA-Agent"
    workstream_id: str = "general"
    allowed_paths: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
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


class ProposalDiscussionResponse(BaseModel):
    reply_body: str
    action: str = "pending"
    title: str = ""
    summary: str = ""
    version_bump: str = ""
    module: str = ""


class _IssueRecord(BaseModel):
    title: str
    url: str = ""
    error: str = ""


ROLE_PRODUCT_MANAGER = crewai_role_registry.ROLE_PRODUCT_MANAGER
ROLE_TEST_MANAGER = crewai_role_registry.ROLE_TEST_MANAGER
ROLE_ISSUE_DRAFTER = crewai_role_registry.ROLE_ISSUE_DRAFTER
ROLE_PLAN_REVIEW_AGENT = crewai_role_registry.ROLE_PLAN_REVIEW_AGENT
ROLE_PLAN_QA_AGENT = crewai_role_registry.ROLE_PLAN_QA_AGENT
ROLE_REVIEW_AGENT = crewai_role_registry.ROLE_REVIEW_AGENT
ROLE_QA_AGENT = crewai_role_registry.ROLE_QA_AGENT
ROLE_PROCESS_OPTIMIZATION_ANALYST = crewai_role_registry.ROLE_PROCESS_OPTIMIZATION_ANALYST
ROLE_ISSUE_DISCUSSION_AGENT = crewai_role_registry.ROLE_ISSUE_DISCUSSION_AGENT
ROLE_ISSUE_AUDIT_AGENT = crewai_role_registry.ROLE_ISSUE_AUDIT_AGENT
ROLE_DOCUMENTATION_AGENT = crewai_role_registry.ROLE_DOCUMENTATION_AGENT
ROLE_MILESTONE_MANAGER = crewai_role_registry.ROLE_MILESTONE_MANAGER
ROLE_CODE_QUALITY_ANALYST = crewai_role_registry.ROLE_CODE_QUALITY_ANALYST
ROLE_FEATURE_CODING_AGENT = crewai_role_registry.ROLE_FEATURE_CODING_AGENT
ROLE_BUGFIX_CODING_AGENT = crewai_role_registry.ROLE_BUGFIX_CODING_AGENT
ROLE_PROCESS_OPTIMIZATION_AGENT = crewai_role_registry.ROLE_PROCESS_OPTIMIZATION_AGENT
ROLE_CODE_QUALITY_AGENT = crewai_role_registry.ROLE_CODE_QUALITY_AGENT

MODULE_ALIASES = {
    "runtime": "Runtime",
    "self-upgrade": "Self-Upgrade",
    "self-upgrade-runtime": "Self-Upgrade",
    "ci": "CI",
    "doctor": "Doctor",
    "bootstrap": "Bootstrap",
    "workspace": "Workspace",
    "github-project": "GitHub-Project",
    "delivery": "Delivery",
    "proposal": "Proposal",
    "review": "Review",
    "qa": "QA",
    "cli": "CLI",
    "hub": "Hub",
    "release": "Release",
    "requirements": "Requirements",
    "observability": "Observability",
    "security": "Security",
    "quality": "Quality",
}

MODULE_RULES: list[tuple[tuple[str, ...], str]] = [
    ((".github/workflows", "workflow", "ci", "github actions"), "CI"),
    (("bootstrap_and_run.py", "run.sh", "bootstrap"), "Bootstrap"),
    (("doctor.py", " doctor", "doctor "), "Doctor"),
    (("workspace", "worktree", "worktrees", "workspace_store"), "Workspace"),
    (("panel_github_sync", "github_projects", "github issue", "github project"), "GitHub-Project"),
    (("delivery", "review", "qa", "release"), "Delivery"),
    (("proposal", "discussion"), "Proposal"),
    (("teamos", " cli ", " cli/", "/teamos"), "CLI"),
    (("temporal", "postgres", "redis", "hub"), "Hub"),
    (("requirements", "raw_inputs", "requirement"), "Requirements"),
    (("observability", "metrics", "telemetry", "heartbeat"), "Observability"),
    (("security", "auth", "oauth", "token"), "Security"),
    (("crewai_self_upgrade", "self_upgrade", "self-upgrade"), "Self-Upgrade"),
    (("control-plane", "orchestrator", "main.py", "runtime"), "Runtime"),
]


class LocalizedWorkItemText(BaseModel):
    title: str = ""
    summary: str = ""
    acceptance: list[str] = Field(default_factory=list)


class LocalizedFindingText(BaseModel):
    title: str = ""
    summary: str = ""
    rationale: str = ""
    acceptance: list[str] = Field(default_factory=list)
    work_items: list[LocalizedWorkItemText] = Field(default_factory=list)


class LocalizedProposalText(BaseModel):
    title: str = ""
    summary: str = ""
    rationale: str = ""
    work_items: list[LocalizedWorkItemText] = Field(default_factory=list)


class LocalizedTaskText(BaseModel):
    task_title: str = ""
    title: str = ""
    summary: str = ""
    rationale: str = ""
    acceptance: list[str] = Field(default_factory=list)


def _env_truthy(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default) or "").strip().lower() not in ("", "0", "false", "no", "off")


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ts_compact_utc() -> str:
    return _utc_now_iso().replace(":", "").replace("-", "")


def _slug(text: str, *, default: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return s or default


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")
role_display_zh = crewai_role_registry.role_display_zh


def _module_slug(module: str) -> str:
    return _slug(module, default="self-upgrade")


def _normalize_module_name(
    raw: str = "",
    *,
    paths: Optional[list[str]] = None,
    workstream_id: str = "",
    title: str = "",
    summary: str = "",
    lane: str = "",
) -> str:
    raw_slug = _module_slug(raw)
    if raw_slug in MODULE_ALIASES:
        return MODULE_ALIASES[raw_slug]

    bag = " | ".join(
        [
            str(raw or ""),
            str(workstream_id or ""),
            str(title or ""),
            str(summary or ""),
            " ".join([str(x).strip() for x in (paths or []) if str(x).strip()]),
        ]
    ).lower()
    for needles, module in MODULE_RULES:
        if any(needle in bag for needle in needles):
            return module

    if raw_slug and raw_slug not in ("item", "general"):
        return "-".join([part.capitalize() for part in raw_slug.split("-") if part]) or "Self-Upgrade"
    if str(lane or "").strip().lower() == "bug":
        return "Runtime"
    return "Self-Upgrade"


def _normalize_repo_doc_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/").lstrip("/")
    while raw.startswith("./"):
        raw = raw[2:]
    return raw


def _documentation_allowed_paths(*, module: str, lane: str, allowed_paths: list[str]) -> list[str]:
    out = ["README.md", "docs", "templates/runtime/README.md"]
    module_norm = _normalize_module_name(module, paths=allowed_paths, lane=lane)
    lane_norm = str(lane or "").strip().lower()
    path_bag = [_normalize_repo_doc_path(x) for x in (allowed_paths or []) if _normalize_repo_doc_path(x)]
    if module_norm in ("CLI", "Doctor", "Bootstrap", "Runtime", "Self-Upgrade", "CI", "Release", "GitHub-Project"):
        out.extend(
            [
                "docs/EXECUTION_RUNBOOK.md",
                "docs/REPO_BOOTSTRAP_AND_UPGRADE.md",
                "docs/GOVERNANCE.md",
            ]
        )
    if module_norm == "CI":
        out.append(".github/ISSUE_TEMPLATE")
    if lane_norm == "process":
        out.append("docs/plan")
    if any(path.startswith(".github/workflows") for path in path_bag):
        out.extend([".github/ISSUE_TEMPLATE", "docs/GOVERNANCE.md"])
    return sorted({x for x in out if str(x).strip()})


def _default_documentation_policy(*, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> dict[str, Any]:
    lane = str(finding.lane or "").strip().lower()
    module = _normalize_module_name(
        str(work_item.module or finding.module or "").strip(),
        paths=list(work_item.allowed_paths or finding.files or []),
        workstream_id=str(work_item.workstream_id or finding.workstream_id or ""),
        title=str(work_item.title or finding.title or ""),
        summary=str(work_item.summary or finding.summary or ""),
        lane=lane,
    )
    paths = [_normalize_repo_doc_path(x) for x in (work_item.allowed_paths or finding.files or []) if _normalize_repo_doc_path(x)]
    reasons: list[str] = []
    if lane in ("feature", "process"):
        reasons.append("该任务属于功能增强或流程优化，默认需要同步说明文档。")
    if module in ("CLI", "Doctor", "Bootstrap", "Runtime", "Self-Upgrade", "CI", "Release", "GitHub-Project"):
        reasons.append(f"模块 `{module}` 对外行为或运维流程敏感，需要记录使用/回滚说明。")
    if any(path == "README.md" or path.startswith("docs/") or path.startswith(".github/workflows") or path == "teamos" for path in paths):
        reasons.append("任务涉及入口脚本、文档或工作流路径，需要同步说明和操作手册。")
    required = bool(reasons)
    if not reasons:
        reasons.append("当前任务以局部缺陷修复为主，默认不强制更新文档。")
    return {
        "required": required,
        "status": "pending" if required else "not_required",
        "allowed_paths": _documentation_allowed_paths(module=module, lane=lane, allowed_paths=list(work_item.allowed_paths or finding.files or [])),
        "rationale": " ".join(reasons),
        "documentation_role": ROLE_DOCUMENTATION_AGENT,
    }


def _lane_requires_user_confirmation(lane: str) -> bool:
    return crewai_workflow_registry.workflow_for_lane(lane).requires_user_confirmation


def _issue_type_token(lane: str) -> str:
    return {"feature": "Feature", "bug": "Bug", "process": "Process", "quality": "Quality"}.get(str(lane or "").strip().lower(), "Task")


def _version_label(version_bump: str) -> str:
    vb = str(version_bump or "none").strip().lower()
    return f"version:{vb if vb in ('major', 'minor', 'patch', 'none') else 'none'}"


def _proposal_status_label(status: str) -> str:
    raw = str(status or "PENDING_CONFIRMATION").strip().upper()
    mapping = {
        "PENDING_CONFIRMATION": "proposal:pending-confirmation",
        "APPROVED": "proposal:approved",
        "HOLD": "proposal:hold",
        "REJECTED": "proposal:rejected",
        "MATERIALIZED": "proposal:materialized",
        "COLLECTING": "proposal:hold",
    }
    return mapping.get(raw, "proposal:pending-confirmation")


def _milestone_title_for_target_version(version: str) -> str:
    ver = str(version or "").strip()
    if not ver or not re.search(r"^\d+\.\d+\.\d+$", ver):
        return ""
    return f"v{ver}"


def _milestone_id_for_title(title: str) -> str:
    return _module_slug(title or "")


def _release_line_for_finding(finding: UpgradeFinding) -> str:
    lane = str(finding.lane or "").strip().lower()
    version_bump = str(finding.version_bump or "").strip().lower()
    if lane == "quality":
        return "none"
    if lane == "bug" or version_bump == "patch":
        return "patch"
    if version_bump == "major":
        return "major"
    if version_bump == "minor":
        return "minor"
    return "none"


def _milestone_schedule(release_line: str) -> tuple[str, str, str]:
    import datetime as _dt

    today = _dt.datetime.now(_dt.timezone.utc).date()
    lead_days = {
        "patch": 7,
        "minor": 14,
        "major": 30,
    }.get(str(release_line or "").strip().lower(), 10)
    target = today + _dt.timedelta(days=lead_days)
    start_date = today.isoformat()
    target_date = target.isoformat()
    due_on = f"{target_date}T00:00:00Z"
    return start_date, target_date, due_on


def _milestone_state_from_metrics(*, total_items: int, blocked_items: int, done_items: int) -> str:
    if total_items <= 0:
        return "draft"
    if blocked_items > 0 and done_items < total_items:
        return "blocked"
    if done_items >= total_items:
        return "release-candidate"
    return "active"


def _release_issue_marker(*, project_id: str, milestone_id: str) -> str:
    return f"<!-- teamos:release-milestone:{str(project_id or '').strip()}:{str(milestone_id or '').strip()} -->"


def _release_issue_title(*, milestone_title: str) -> str:
    return f"[Process][Release] 跟踪 {str(milestone_title or '').strip()} 版本发布".strip()


def _proposal_issue_marker(doc: dict[str, Any]) -> str:
    return f"<!-- teamos:feature-proposal:{str(doc.get('proposal_id') or '').strip()} -->"


def _task_issue_marker(*, repo_locator: str, repo_root: Path, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> str:
    fingerprint = _finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding) + "-" + _slug(work_item.title, default="work")
    return f"<!-- teamos:self_upgrade:{fingerprint} -->"


def _normalize_owner_role(role_id: str, lane: str) -> str:
    rid = str(role_id or "").strip()
    if rid in ("", "Coding-Agent", "Developer", "Developer-Agent"):
        return _coding_owner_role(lane)
    return rid


def _normalize_review_role(role_id: str) -> str:
    rid = str(role_id or "").strip()
    if rid in ("", "Review Agent"):
        return ROLE_REVIEW_AGENT
    return rid


def _normalize_qa_role(role_id: str) -> str:
    rid = str(role_id or "").strip()
    if rid in ("", "QA Agent"):
        return ROLE_QA_AGENT
    return rid


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _looks_english(text: str) -> bool:
    s = str(text or "").strip()
    return bool(s) and bool(_ASCII_WORD_RE.search(s)) and not _has_cjk(s)


def _normalize_issue_text(text: str, *, empty_fallback: str = "(空)") -> str:
    s = str(text or "").strip()
    return s or empty_fallback


def _zh_localization_enabled() -> bool:
    return _env_truthy("TEAMOS_SELF_UPGRADE_LOCALIZE_ZH", "1")


def _git_dir(repo_root: Path) -> Optional[Path]:
    dotgit = repo_root / ".git"
    if dotgit.is_dir():
        return dotgit
    if dotgit.is_file():
        raw = dotgit.read_text(encoding="utf-8", errors="replace").strip()
        if raw.startswith("gitdir:"):
            return (repo_root / raw.split(":", 1)[1].strip()).resolve()
    return None


def _head_ref(repo_root: Path) -> tuple[str, str]:
    gd = _git_dir(repo_root)
    if gd is None:
        return "", ""
    head_path = gd / "HEAD"
    if not head_path.exists():
        return "", ""
    raw = head_path.read_text(encoding="utf-8", errors="replace").strip()
    if raw.startswith("ref:"):
        ref = raw.split(":", 1)[1].strip()
        return ref.rsplit("/", 1)[-1], ref
    return "", raw


def _origin_url(repo_root: Path) -> str:
    gd = _git_dir(repo_root)
    if gd is None:
        return ""
    cfg = gd / "config"
    if not cfg.exists():
        return ""
    parser = configparser.ConfigParser()
    try:
        parser.read(cfg, encoding="utf-8")
    except Exception:
        return ""
    section = 'remote "origin"'
    return str(parser.get(section, "url", fallback="") or "").strip()


def _parse_repo_locator(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    patterns = [
        r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$",
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$",
    ]
    for pat in patterns:
        m = re.search(pat, raw)
        if m:
            owner = str(m.group("owner") or "").strip()
            name = str(m.group("name") or "").strip()
            if owner and name:
                return f"{owner}/{name}"
    return ""


def _read_text(path: Path, *, max_chars: int = 4000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return ""


def _sample_files(root: Path, pattern: str, *, limit: int = 20) -> list[str]:
    out: list[str] = []
    try:
        for p in sorted(root.glob(pattern)):
            if not p.is_file():
                continue
            out.append(str(p.relative_to(root)))
            if len(out) >= limit:
                break
    except Exception:
        return []
    return out


_SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".sh"}
_SOURCE_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".idea",
    ".vscode",
}


def _walk_source_files(root: Path, *, limit: int = 400) -> list[Path]:
    out: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in sorted(dirnames) if d not in _SOURCE_SKIP_DIRS]
            base = Path(dirpath)
            for name in sorted(filenames):
                path = base / name
                if path.suffix.lower() not in _SOURCE_EXTENSIONS:
                    continue
                out.append(path)
                if len(out) >= limit:
                    return out
    except Exception:
        return out
    return out


def _source_inventory(root: Path) -> dict[str, Any]:
    files = _walk_source_files(root, limit=400)
    sample = [str(p.relative_to(root)) for p in files[:40]]
    largest = [
        {"path": str(p.relative_to(root)), "bytes": int(p.stat().st_size)}
        for p in sorted(files, key=lambda x: x.stat().st_size if x.exists() else 0, reverse=True)[:12]
    ]
    basename_map: dict[str, list[str]] = {}
    for path in files:
        basename_map.setdefault(path.name.lower(), []).append(str(path.relative_to(root)))
    duplicates = [
        {"basename": basename, "paths": paths[:6]}
        for basename, paths in sorted(basename_map.items())
        if len(paths) > 1
    ][:12]
    stale_candidates: list[str] = []
    for path in files:
        rel = str(path.relative_to(root))
        rel_lower = rel.lower()
        if any(token in rel_lower for token in ("/legacy/", "/deprecated/", "/archive/", "/old/", ".bak", ".old", "copy.py", "copy.ts", "copy.js")):
            stale_candidates.append(rel)
        if len(stale_candidates) >= 20:
            break
    return {
        "source_files_sample": sample,
        "largest_source_files": largest,
        "duplicate_basename_candidates": duplicates,
        "stale_file_candidates": stale_candidates,
    }


def _safe_project_id(raw: str) -> str:
    base = _slug(raw, default="project").replace("-", "_")
    if not base:
        return "project"
    return base[:64]


def _panel_project_id(requested_project_id: str) -> str:
    pid = str(requested_project_id or "").strip() or "teamos"
    try:
        mapping = load_mapping()
        if get_project_cfg(mapping, pid):
            return pid
    except PanelMappingError:
        pass
    return "teamos"


def _runtime_root() -> Path:
    raw = str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (team_os_root().parent / "team-os-runtime").resolve()


def _worktrees_root() -> Path:
    return (_runtime_root() / "workspace" / "worktrees").resolve()


def _worktree_hint_parts(raw_hint: str) -> list[str]:
    raw = str(raw_hint or "").strip()
    if not raw:
        return []
    hint_path = Path(raw).expanduser()
    parts = [str(part).strip() for part in hint_path.parts if str(part).strip() and str(part).strip() not in (os.sep, ".", "..")]
    lower = [part.lower() for part in parts]
    if hint_path.is_absolute():
        if "worktrees" in lower:
            parts = parts[lower.index("worktrees") + 1 :]
        elif "upgrade" in lower:
            parts = parts[lower.index("upgrade") :]
        else:
            parts = parts[-1:]
    return [_slug(part, default="item") for part in parts if str(part).strip()]


def _normalize_worktree_hint(*, repo_root: Path, lane: str, title: str, raw_hint: str = "") -> str:
    parts = _worktree_hint_parts(raw_hint)
    if parts:
        return str((_worktrees_root().joinpath(*parts)).resolve())
    return str((_worktrees_root() / f"{_slug(repo_root.name)}-{_slug(lane)}-{_slug(title)[:24]}").resolve())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_state(target_id: str) -> dict[str, Any]:
    return improvement_store.load_target_state(str(target_id or "").strip())


def _read_run_history(*, target_id: str, limit: int = 12) -> list[dict[str, Any]]:
    state = _read_state(target_id)
    rows = state.get("history")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows[-max(1, int(limit)) :]:
        if isinstance(row, dict):
            out.append(dict(row))
    return out


def _append_run_history(target_id: str, entry: dict[str, Any], *, keep: int = 30) -> None:
    improvement_store.append_target_history(str(target_id or "").strip(), dict(entry or {}), keep=keep)


def _merge_state_last_run(target_id: str, last_run: dict[str, Any], *, backoff_until: str = "") -> None:
    improvement_store.merge_target_last_run(str(target_id or "").strip(), dict(last_run or {}), backoff_until=backoff_until)


def _should_skip(*, target_id: str, repo_root: Path, force: bool) -> tuple[bool, str]:
    if force:
        return False, ""
    state = _read_state(target_id)
    backoff_until = str(state.get("backoff_until") or "").strip()
    if not backoff_until:
        return False, ""
    try:
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        hold = _dt.datetime.fromisoformat(backoff_until.replace("Z", "+00:00"))
        if now < hold:
            return True, f"failure_backoff_until:{backoff_until}"
    except Exception:
        return False, ""
    return False, ""


def _resolve_target(*, target_id: str, repo_path: str, repo_url: str, repo_locator: str, project_id: str) -> dict[str, Any]:
    target = improvement_store.ensure_target(
        project_id=str(project_id or "teamos").strip() or "teamos",
        target_id=str(target_id or "").strip(),
        repo_path=str(repo_path or "").strip(),
        repo_url=str(repo_url or "").strip(),
        repo_locator=str(repo_locator or "").strip(),
    )
    return improvement_store.materialize_target_repo(target)


def collect_repo_context(*, repo_root: Path, explicit_repo_locator: str = "", target_id: str = "") -> dict[str, Any]:
    repo_root = repo_root.resolve()
    locator = str(explicit_repo_locator or "").strip()
    origin = _origin_url(repo_root)
    if not locator:
        locator = _parse_repo_locator(origin)
    branch, head_ref = _head_ref(repo_root)
    head_commit = ""
    gd = _git_dir(repo_root)
    if gd is not None:
        if head_ref:
            ref_path = gd / head_ref
            if ref_path.exists():
                head_commit = ref_path.read_text(encoding="utf-8", errors="replace").strip()
        elif branch:
            head_commit = head_ref
    status_out = ""
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=15,
        )
        if p.returncode == 0:
            status_out = str(p.stdout or "")
    except Exception:
        status_out = ""

    readme = ""
    for name in ("README.md", "README", "readme.md"):
        readme = _read_text(repo_root / name, max_chars=3000)
        if readme:
            break

    dep_files = []
    for rel in ("pyproject.toml", "requirements.txt", "package.json", "setup.py", "setup.cfg"):
        if (repo_root / rel).exists():
            dep_files.append(rel)

    workflow_files = _sample_files(repo_root, ".github/workflows/*", limit=20)
    test_files = _sample_files(repo_root, "tests/test_*.py", limit=25)
    if not test_files:
        test_files = _sample_files(repo_root, "test_*.py", limit=25)

    top_level = []
    try:
        for child in sorted(repo_root.iterdir()):
            top_level.append(child.name + ("/" if child.is_dir() else ""))
            if len(top_level) >= 40:
                break
    except Exception:
        pass
    source_inventory = _source_inventory(repo_root)

    return {
        "repo_root": str(repo_root),
        "repo_name": repo_root.name,
        "repo_locator": locator,
        "origin_url": origin,
        "current_branch": branch,
        "git_status_dirty": bool(status_out),
        "git_status_sample": status_out.splitlines()[:40] if status_out else [],
        "head_commit": head_commit,
        "top_level_entries": top_level,
        "dependency_files": dep_files,
        "workflow_files": workflow_files,
        "test_files": test_files,
        "has_github_actions": bool(workflow_files),
        "readme_excerpt": readme,
        "current_version": _read_current_version(repo_root),
        "recent_execution_metrics": _recent_execution_metrics(target_id=str(target_id or "").strip(), limit=8),
        **source_inventory,
    }


def _codex_structured_model() -> str:
    return str(os.getenv("TEAMOS_CREWAI_MODEL") or os.getenv("OPENAI_MODEL") or "openai-codex/gpt-5.3-codex").strip()


def _codex_structured(prompt: str, *, schema_model: type[BaseModel], timeout_sec: int = 120) -> BaseModel:
    result = codex_llm.codex_exec_structured(
        prompt=prompt,
        schema=schema_model.model_json_schema(),
        timeout_sec=timeout_sec,
        model=_codex_structured_model(),
    )
    return schema_model.model_validate(result.data)


def _translate_to_zh_structured(*, payload: dict[str, Any], schema_model: type[BaseModel], prompt_title: str) -> BaseModel:
    prompt = "\n".join(
        [
            f"你是 Team OS 的中文化助手。请把下面的 {prompt_title} 翻译成简体中文。",
            "要求：",
            "- 只翻译自然语言文本。",
            "- 保留 role id、状态枚举、版本号、issue/proposal/task id、路径、命令、URL、标签名、worktree_hint、repo_locator 原样不变。",
            "- 输出必须符合给定 JSON Schema。",
            "- 不要增加新字段，不要删除现有字段。",
            "",
            "输入 JSON：",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )
    return _codex_structured(prompt, schema_model=schema_model, timeout_sec=150)


def _localize_finding_to_zh(finding: UpgradeFinding) -> UpgradeFinding:
    if not _zh_localization_enabled():
        return finding
    texts = [finding.title, finding.summary, finding.rationale, *list(finding.acceptance or [])]
    texts.extend([w.title for w in (finding.work_items or [])])
    texts.extend([w.summary for w in (finding.work_items or [])])
    texts.extend([x for w in (finding.work_items or []) for x in (w.acceptance or [])])
    if not any(_looks_english(x) for x in texts):
        return finding
    try:
        localized = _translate_to_zh_structured(
            payload={
                "title": finding.title,
                "summary": finding.summary,
                "rationale": finding.rationale,
                "acceptance": list(finding.acceptance or []),
                "work_items": [
                    {
                        "title": str(w.title or ""),
                        "summary": str(w.summary or ""),
                        "acceptance": list(w.acceptance or []),
                    }
                    for w in (finding.work_items or [])
                ],
            },
            schema_model=LocalizedFindingText,
            prompt_title="自升级发现项",
        )
    except Exception:
        return finding
    work_items = list(finding.work_items or [])
    localized_items = list(localized.work_items or [])
    out_items: list[UpgradeWorkItem] = []
    for idx, work_item in enumerate(work_items):
        patch = localized_items[idx] if idx < len(localized_items) else LocalizedWorkItemText()
        out_items.append(
            work_item.model_copy(
                update={
                    "title": str(patch.title or work_item.title).strip() or work_item.title,
                    "summary": str(patch.summary or work_item.summary).strip() or work_item.summary,
                    "acceptance": [str(x).strip() for x in (patch.acceptance or work_item.acceptance or []) if str(x).strip()],
                }
            )
        )
    return finding.model_copy(
        update={
            "title": str(localized.title or finding.title).strip() or finding.title,
            "summary": str(localized.summary or finding.summary).strip() or finding.summary,
            "rationale": str(localized.rationale or finding.rationale).strip() or finding.rationale,
            "acceptance": [str(x).strip() for x in (localized.acceptance or finding.acceptance or []) if str(x).strip()],
            "work_items": out_items,
        }
    )


def _localize_proposal_doc_to_zh(doc: dict[str, Any]) -> dict[str, Any]:
    if not _zh_localization_enabled():
        return dict(doc)
    texts = [doc.get("title"), doc.get("summary"), doc.get("rationale")]
    texts.extend([str((x or {}).get("title") or "") for x in (doc.get("work_items") or []) if isinstance(x, dict)])
    texts.extend([str((x or {}).get("summary") or "") for x in (doc.get("work_items") or []) if isinstance(x, dict)])
    if not any(_looks_english(x) for x in texts):
        return dict(doc)
    try:
        localized = _translate_to_zh_structured(
            payload={
                "title": str(doc.get("title") or ""),
                "summary": str(doc.get("summary") or ""),
                "rationale": str(doc.get("rationale") or ""),
                "work_items": [
                    {
                        "title": str((x or {}).get("title") or ""),
                        "summary": str((x or {}).get("summary") or ""),
                        "acceptance": list(((x or {}).get("acceptance") or [])),
                    }
                    for x in (doc.get("work_items") or [])
                    if isinstance(x, dict)
                ],
            },
            schema_model=LocalizedProposalText,
            prompt_title="功能提案",
        )
    except Exception:
        return dict(doc)
    out = dict(doc)
    out["title"] = str(localized.title or doc.get("title") or "").strip() or str(doc.get("title") or "")
    out["summary"] = str(localized.summary or doc.get("summary") or "").strip() or str(doc.get("summary") or "")
    out["rationale"] = str(localized.rationale or doc.get("rationale") or "").strip() or str(doc.get("rationale") or "")
    out["module"] = _normalize_module_name(
        str(doc.get("module") or "").strip(),
        paths=[str(x).strip() for x in (doc.get("files") or []) if str(x).strip()],
        workstream_id=str(doc.get("workstream_id") or ""),
        title=str(out.get("title") or ""),
        summary=str(out.get("summary") or ""),
        lane=str(doc.get("lane") or ""),
    )
    items = []
    localized_items = list(localized.work_items or [])
    lane = str(doc.get("lane") or "feature").strip().lower() or "feature"
    for idx, raw in enumerate(list(doc.get("work_items") or [])):
        item = dict(raw) if isinstance(raw, dict) else {}
        patch = localized_items[idx] if idx < len(localized_items) else LocalizedWorkItemText()
        item["title"] = str(patch.title or item.get("title") or "").strip() or str(item.get("title") or "")
        item["summary"] = str(patch.summary or item.get("summary") or "").strip() or str(item.get("summary") or "")
        if patch.acceptance:
            item["acceptance"] = [str(x).strip() for x in patch.acceptance if str(x).strip()]
        item["owner_role"] = _normalize_owner_role(str(item.get("owner_role") or "").strip(), lane)
        item["review_role"] = _normalize_review_role(str(item.get("review_role") or "").strip())
        item["qa_role"] = _normalize_qa_role(str(item.get("qa_role") or "").strip())
        item["module"] = _normalize_module_name(
            str(item.get("module") or out.get("module") or "").strip(),
            paths=[str(x).strip() for x in (item.get("allowed_paths") or doc.get("files") or []) if str(x).strip()],
            workstream_id=str(item.get("workstream_id") or doc.get("workstream_id") or "general").strip(),
            title=str(item.get("title") or ""),
            summary=str(item.get("summary") or ""),
            lane=lane,
        )
        items.append(item)
    out["work_items"] = items
    return out


def _localize_task_doc_to_zh(doc: dict[str, Any]) -> dict[str, Any]:
    if not _zh_localization_enabled():
        return dict(doc)
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        su = {}
    work_item = su.get("work_item") or {}
    if not isinstance(work_item, dict):
        work_item = {}
    texts = [doc.get("title"), su.get("summary"), su.get("rationale"), work_item.get("title"), work_item.get("summary")]
    texts.extend(list(su.get("acceptance") or []))
    texts.extend(list(work_item.get("acceptance") or []))
    if not any(_looks_english(x) for x in texts):
        return dict(doc)
    try:
        localized = _translate_to_zh_structured(
            payload={
                "task_title": str(doc.get("title") or ""),
                "title": str(work_item.get("title") or ""),
                "summary": str(work_item.get("summary") or su.get("summary") or ""),
                "rationale": str(su.get("rationale") or ""),
                "acceptance": list(work_item.get("acceptance") or su.get("acceptance") or []),
            },
            schema_model=LocalizedTaskText,
            prompt_title="自升级任务单",
        )
    except Exception:
        return dict(doc)
    out = dict(doc)
    if localized.task_title:
        out["title"] = str(localized.task_title).strip()
    su_out = dict(su)
    if localized.summary:
        su_out["summary"] = str(localized.summary).strip()
    if localized.rationale:
        su_out["rationale"] = str(localized.rationale).strip()
    if localized.acceptance:
        su_out["acceptance"] = [str(x).strip() for x in localized.acceptance if str(x).strip()]
    wi_out = dict(work_item)
    if localized.title:
        wi_out["title"] = str(localized.title).strip()
    if localized.summary:
        wi_out["summary"] = str(localized.summary).strip()
    if localized.acceptance:
        wi_out["acceptance"] = [str(x).strip() for x in localized.acceptance if str(x).strip()]
    lane = str(su.get("lane") or "bug").strip().lower() or "bug"
    wi_out["owner_role"] = _normalize_owner_role(str(wi_out.get("owner_role") or out.get("owner_role") or "").strip(), lane)
    wi_out["review_role"] = _normalize_review_role(str(wi_out.get("review_role") or ((out.get("execution_policy") or {}) if isinstance(out.get("execution_policy"), dict) else {}).get("review_role") or "").strip())
    wi_out["qa_role"] = _normalize_qa_role(str(wi_out.get("qa_role") or ((out.get("execution_policy") or {}) if isinstance(out.get("execution_policy"), dict) else {}).get("qa_role") or "").strip())
    wi_out["module"] = _normalize_module_name(
        str(wi_out.get("module") or su.get("module") or "").strip(),
        paths=[str(x).strip() for x in (wi_out.get("allowed_paths") or ((out.get("execution_policy") or {}) if isinstance(out.get("execution_policy"), dict) else {}).get("allowed_paths") or su.get("files") or []) if str(x).strip()],
        workstream_id=str(wi_out.get("workstream_id") or out.get("workstream_id") or su.get("workstream_id") or "general").strip(),
        title=str(wi_out.get("title") or out.get("title") or ""),
        summary=str(wi_out.get("summary") or su_out.get("summary") or ""),
        lane=lane,
    )
    su_out["module"] = _normalize_module_name(
        str(su_out.get("module") or wi_out.get("module") or "").strip(),
        paths=[str(x).strip() for x in (su_out.get("files") or wi_out.get("allowed_paths") or []) if str(x).strip()],
        workstream_id=str(out.get("workstream_id") or su_out.get("workstream_id") or "general").strip(),
        title=str(out.get("title") or wi_out.get("title") or ""),
        summary=str(su_out.get("summary") or wi_out.get("summary") or ""),
        lane=lane,
    )
    su_out["work_item"] = wi_out
    out["self_upgrade"] = su_out
    out["owner_role"] = wi_out["owner_role"]
    out["owners"] = [wi_out["owner_role"]]
    out["roles_involved"] = [wi_out["owner_role"], wi_out["review_role"], wi_out["qa_role"]]
    execution_policy = out.get("execution_policy") or {}
    if isinstance(execution_policy, dict):
        execution_policy = dict(execution_policy)
        execution_policy["review_role"] = wi_out["review_role"]
        execution_policy["qa_role"] = wi_out["qa_role"]
        execution_policy["module"] = wi_out["module"]
        execution_policy["commit_message_template"] = f"{str(out.get('id') or '').strip() or 'TASK'}: {str(wi_out.get('title') or out.get('title') or '').strip()}"
        out["execution_policy"] = execution_policy
    return out


def _localize_discussion_response_to_zh(reply: ProposalDiscussionResponse) -> ProposalDiscussionResponse:
    if not _zh_localization_enabled():
        return reply
    texts = [reply.reply_body, reply.title, reply.summary]
    if not any(_looks_english(x) for x in texts):
        return reply
    try:
        localized = _translate_to_zh_structured(
            payload=reply.model_dump(),
            schema_model=ProposalDiscussionResponse,
            prompt_title="需求讨论回复",
        )
    except Exception:
        return reply
    return ProposalDiscussionResponse.model_validate(
        {
            "reply_body": str(localized.reply_body or reply.reply_body).strip() or reply.reply_body,
            "action": str(localized.action or reply.action).strip() or reply.action,
            "title": str(localized.title or reply.title).strip(),
            "summary": str(localized.summary or reply.summary).strip(),
            "version_bump": str(localized.version_bump or reply.version_bump).strip(),
            "module": str(reply.module or "").strip(),
        }
    )


_SEMVER_RE = re.compile(r"\b(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)\b")


def _read_current_version(repo_root: Path) -> str:
    candidates = [
        repo_root / "pyproject.toml",
        repo_root / "package.json",
        repo_root / "VERSION",
        repo_root / "version.txt",
    ]
    for path in candidates:
        text = _read_text(path, max_chars=8000)
        if not text:
            continue
        match = _SEMVER_RE.search(text)
        if match:
            return match.group(0)
    return "0.1.0"


def _bump_version(version: str, bump: str) -> str:
    match = _SEMVER_RE.search(str(version or "").strip())
    if not match:
        match = _SEMVER_RE.search("0.1.0")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    mode = str(bump or "patch").strip().lower()
    if mode == "major":
        return f"{major + 1}.0.0"
    if mode == "minor":
        return f"{major}.{minor + 1}.0"
    if mode == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return f"{major}.{minor}.{patch}"


def _lane_default_version_bump(lane: str) -> str:
    return crewai_workflow_registry.workflow_for_lane(lane).default_version_bump


def _lane_default_cooldown_hours(lane: str, *, requires_user_confirmation: bool) -> int:
    _ = requires_user_confirmation
    return crewai_workflow_registry.workflow_for_lane(lane).cooldown_hours()


def _lane_default_baseline_action(lane: str, version_bump: str) -> str:
    return crewai_workflow_registry.workflow_for_lane(lane).default_baseline_action(version_bump)


def _coding_owner_role(lane: str) -> str:
    ln = str(lane or "bug").strip().lower()
    if ln == "feature":
        return ROLE_FEATURE_CODING_AGENT
    if ln == "bug":
        return ROLE_BUGFIX_CODING_AGENT
    if ln == "quality":
        return ROLE_CODE_QUALITY_AGENT
    return ROLE_PROCESS_OPTIMIZATION_AGENT


def _worktree_hint(*, repo_root: Path, lane: str, title: str) -> str:
    return _normalize_worktree_hint(repo_root=repo_root, lane=lane, title=title)


def _default_work_items(*, repo_root: Path, finding: UpgradeFinding) -> list[UpgradeWorkItem]:
    return [
        UpgradeWorkItem(
            title=str(finding.title or "").strip(),
            summary=str(finding.summary or "").strip(),
            owner_role=_coding_owner_role(finding.lane),
            review_role=ROLE_REVIEW_AGENT,
            qa_role=ROLE_QA_AGENT,
            workstream_id=finding.workstream_id or "general",
            allowed_paths=list(finding.files or []),
            tests=list(finding.tests or []),
            acceptance=list(finding.acceptance or []),
            worktree_hint=_worktree_hint(repo_root=repo_root, lane=finding.lane, title=finding.title),
            module=_normalize_module_name(
                str(finding.module or "").strip(),
                paths=list(finding.files or []),
                workstream_id=str(finding.workstream_id or ""),
                title=str(finding.title or ""),
                summary=str(finding.summary or ""),
                lane=str(finding.lane or ""),
            ),
        )
    ]


def _recent_execution_metrics(*, target_id: str = "", limit: int = 8) -> list[dict[str, Any]]:
    tid = str(target_id or "").strip()
    if not tid:
        return []
    return _read_run_history(target_id=tid, limit=limit)


def _planned_version(current_version: str, findings: list[UpgradeFinding]) -> str:
    priorities = {"major": 3, "minor": 2, "patch": 1, "none": 0}
    highest = max((priorities.get(str(f.version_bump or "none"), 0) for f in findings), default=0)
    bump = "none"
    for key, val in priorities.items():
        if val == highest:
            bump = key
            break
    return _bump_version(current_version, bump)


def _crewai_llm():
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    crewai_runtime.require_crewai_importable(refresh=True)
    from crewai.llm import LLM

    model = str(os.getenv("TEAMOS_CREWAI_MODEL") or os.getenv("OPENAI_MODEL") or "openai-codex/gpt-5.3-codex").strip()
    base_url = str(os.getenv("TEAMOS_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or "").strip()
    api_key = str(os.getenv("TEAMOS_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    auth_mode = str(os.getenv("TEAMOS_CREWAI_AUTH_MODE") or os.getenv("CREWAI_OPENAI_AUTH_MODE") or "").strip().lower()

    logged_in = False
    if "codex" in model.lower():
        try:
            logged_in, _ = codex_llm.codex_login_status()
        except codex_llm.CodexUnavailable:
            logged_in = False

    if logged_in and "codex" in model.lower():
        os.environ["CREWAI_OPENAI_AUTH_MODE"] = "oauth_codex"
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENAI_OAUTH_ACCESS_TOKEN", None)
        os.environ.pop("OPENAI_ACCESS_TOKEN", None)
        api_key = ""
        base_url = ""
    elif (not auth_mode) and ("codex" in model.lower()) and (not api_key):
        os.environ["CREWAI_OPENAI_AUTH_MODE"] = "oauth_codex"

    kwargs: dict[str, Any] = {
        "model": model,
        "api": "responses",
        "is_litellm": False,
        "max_tokens": 4000,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    return LLM(**kwargs)


def _coerce_plan(raw_output: Any, *, max_findings: int, repo_root: Path, current_version: str) -> UpgradePlan:
    obj: Any = None
    if hasattr(raw_output, "to_dict"):
        try:
            obj = raw_output.to_dict()
        except Exception:
            obj = None
    if not obj and hasattr(raw_output, "json_dict"):
        obj = getattr(raw_output, "json_dict", None)
    if obj:
        plan = UpgradePlan.model_validate(obj)
    else:
        text = str(raw_output or "").strip()
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise SelfUpgradeError("CrewAI returned no structured self-upgrade plan")
        plan = UpgradePlan.model_validate(json.loads(match.group(0)))

    findings: list[UpgradeFinding] = []
    for finding in plan.findings[: max(1, int(max_findings))]:
        raw_kind = str(finding.kind or "").strip().upper()
        if raw_kind in ("FEATURE", "OPTIMIZATION"):
            kind = "FEATURE"
        elif raw_kind in ("CODE_QUALITY", "QUALITY", "REFACTOR", "CLEANUP"):
            kind = "CODE_QUALITY"
        elif raw_kind in ("BUG",):
            kind = "BUG"
        else:
            kind = "PROCESS"
        lane = str(getattr(finding, "lane", "") or "").strip().lower()
        if lane not in ("feature", "bug", "process", "quality"):
            lane = {
                "FEATURE": "feature",
                "BUG": "bug",
                "PROCESS": "process",
                "CODE_QUALITY": "quality",
            }.get(kind, "bug")
        impact = str(finding.impact or "MED").strip().upper()
        if impact not in ("LOW", "MED", "HIGH"):
            impact = "MED"
        workstream_id = _slug(finding.workstream_id, default="general")
        version_bump = str(getattr(finding, "version_bump", "") or "").strip().lower()
        if version_bump not in ("major", "minor", "patch", "none"):
            version_bump = _lane_default_version_bump(lane)
        requires_user_confirmation = bool(
            getattr(finding, "requires_user_confirmation", False)
            or _lane_requires_user_confirmation(lane)
        )
        cooldown_hours = int(getattr(finding, "cooldown_hours", 0) or _lane_default_cooldown_hours(lane, requires_user_confirmation=requires_user_confirmation))
        target_version = str(getattr(finding, "target_version", "") or "").strip() or _bump_version(current_version, version_bump)
        if version_bump == "none":
            target_version = current_version
        work_items: list[UpgradeWorkItem] = []
        normalized_module = _normalize_module_name(
            str(getattr(finding, "module", "") or "").strip(),
            paths=[str(x).strip() for x in (finding.files or []) if str(x).strip()],
            workstream_id=workstream_id,
            title=str(finding.title or "").strip(),
            summary=str(finding.summary or "").strip(),
            lane=lane,
        )
        for item in list(getattr(finding, "work_items", []) or [])[:6]:
            title = str(getattr(item, "title", "") or "").strip() or str(finding.title or "").strip() or "Untitled work item"
            work_items.append(
                UpgradeWorkItem(
                    title=title,
                    summary=str(getattr(item, "summary", "") or "").strip() or str(finding.summary or "").strip(),
                    owner_role=_normalize_owner_role(str(getattr(item, "owner_role", "") or "").strip(), lane),
                    review_role=_normalize_review_role(str(getattr(item, "review_role", "") or "").strip()),
                    qa_role=_normalize_qa_role(str(getattr(item, "qa_role", "") or "").strip()),
                    workstream_id=str(getattr(item, "workstream_id", "") or "").strip() or workstream_id or "general",
                    allowed_paths=[str(x).strip() for x in (getattr(item, "allowed_paths", None) or finding.files or []) if str(x).strip()][:20],
                    tests=[str(x).strip() for x in (getattr(item, "tests", None) or finding.tests or []) if str(x).strip()][:20],
                    acceptance=[str(x).strip() for x in (getattr(item, "acceptance", None) or finding.acceptance or []) if str(x).strip()][:20],
                    worktree_hint=_normalize_worktree_hint(
                        repo_root=repo_root,
                        lane=lane,
                        title=title,
                        raw_hint=str(getattr(item, "worktree_hint", "") or "").strip(),
                    ),
                    module=_normalize_module_name(
                        str(getattr(item, "module", "") or "").strip(),
                        paths=[str(x).strip() for x in (getattr(item, "allowed_paths", None) or finding.files or []) if str(x).strip()],
                        workstream_id=str(getattr(item, "workstream_id", "") or "").strip() or workstream_id,
                        title=title,
                        summary=str(getattr(item, "summary", "") or "").strip() or str(finding.summary or "").strip(),
                        lane=lane,
                    )
                    or normalized_module,
                )
            )
        finding_obj = UpgradeFinding(
            kind=kind,
            lane=lane,
            title=str(finding.title or "").strip() or "Untitled finding",
            summary=str(finding.summary or "").strip(),
            module=normalized_module,
            rationale=str(finding.rationale or "").strip(),
            impact=impact,
            workstream_id=workstream_id or "general",
            files=[str(x).strip() for x in (finding.files or []) if str(x).strip()][:20],
            tests=[str(x).strip() for x in (finding.tests or []) if str(x).strip()][:20],
            acceptance=[str(x).strip() for x in (finding.acceptance or []) if str(x).strip()][:20],
            version_bump=version_bump,
            target_version=target_version,
            baseline_action=str(getattr(finding, "baseline_action", "") or "").strip() or _lane_default_baseline_action(lane, version_bump),
            requires_user_confirmation=requires_user_confirmation,
            cooldown_hours=max(0, cooldown_hours),
            work_items=work_items,
        )
        if not finding_obj.work_items and finding_obj.lane in ("feature", "bug", "quality"):
            finding_obj.work_items = _default_work_items(repo_root=repo_root, finding=finding_obj)
        findings.append(finding_obj)
    return UpgradePlan(
        summary=str(plan.summary or "").strip() or "CrewAI self-upgrade analysis completed.",
        findings=findings,
        ci_actions=[str(x).strip() for x in (plan.ci_actions or []) if str(x).strip()][:20],
        notes=[str(x).strip() for x in (plan.notes or []) if str(x).strip()][:20],
        current_version=current_version,
        planned_version=_planned_version(current_version, findings),
    )


def kickoff_upgrade_plan(*, repo_context: dict[str, Any], max_findings: int, verbose: bool = False) -> tuple[UpgradePlan, dict[str, Any]]:
    crewai_runtime.require_crewai_importable()
    from crewai import Crew, Process, Task

    repo_blob = json.dumps(repo_context, ensure_ascii=False, indent=2)
    llm = _crewai_llm()
    product_manager = crewai_agent_factory.build_crewai_agent(role_id=ROLE_PRODUCT_MANAGER, llm=llm, verbose=verbose)
    test_manager = crewai_agent_factory.build_crewai_agent(role_id=ROLE_TEST_MANAGER, llm=llm, verbose=verbose)
    issue_drafter = crewai_agent_factory.build_crewai_agent(role_id=ROLE_ISSUE_DRAFTER, llm=llm, verbose=verbose)
    review_agent = crewai_agent_factory.build_crewai_agent(role_id=ROLE_PLAN_REVIEW_AGENT, llm=llm, verbose=verbose)
    qa_agent = crewai_agent_factory.build_crewai_agent(role_id=ROLE_PLAN_QA_AGENT, llm=llm, verbose=verbose)
    process_analyst = crewai_agent_factory.build_crewai_agent(role_id=ROLE_PROCESS_OPTIMIZATION_ANALYST, llm=llm, verbose=verbose)
    code_quality_analyst = crewai_agent_factory.build_crewai_agent(role_id=ROLE_CODE_QUALITY_ANALYST, llm=llm, verbose=verbose)

    feature_task = Task(
        name="product_feature_scan",
        description=(
            "Analyze the repository context as a product manager.\n"
            f"Return at most {int(max_findings)} feature ideas or product optimizations.\n"
            "Mark changes that should be treated as FEATURE and note whether they imply a major or minor version bump.\n"
            "Use only the supplied context.\n\n"
            f"Repository context:\n{repo_blob}"
        ),
        expected_output="A concise shortlist of feature candidates with evidence and release impact.",
        agent=product_manager,
        markdown=True,
    )
    bug_task = Task(
        name="qa_bug_scan",
        description=(
            "Analyze the repository context as a test manager.\n"
            f"Return at most {int(max_findings)} bug or test-gap candidates.\n"
            "Focus on black-box behavior, white-box coverage gaps, regressions, and CI/test problems.\n"
            "Use only the supplied context.\n\n"
            f"Repository context:\n{repo_blob}"
        ),
        expected_output="A shortlist of bug findings and test weaknesses with concrete evidence.",
        agent=test_manager,
        markdown=True,
    )
    process_task = Task(
        name="process_optimization_scan",
        description=(
            "Review the repository context and recent self-upgrade execution telemetry.\n"
            "Identify process improvements only if they are grounded in recent failures, delays, or workflow friction.\n"
            "Prefer one high-value process improvement over many weak ones."
        ),
        expected_output="A short list of process improvements grounded in run history and telemetry.",
        agent=process_analyst,
        markdown=True,
    )
    quality_task = Task(
        name="code_quality_scan",
        description=(
            "Review the repository context as a code quality analyst.\n"
            f"Return at most {int(max_findings)} code quality candidates.\n"
            "Focus on duplicated logic, unnecessary files, dead/stale code candidates, oversized modules, and reuse/refactor opportunities.\n"
            "Only propose work when the quality gain is concrete and the change can be broken into small, scoped items.\n"
            "Use only the supplied context.\n\n"
            f"Repository context:\n{repo_blob}"
        ),
        expected_output="A shortlist of code quality findings with evidence, cleanup value, and safe refactor boundaries.",
        agent=code_quality_analyst,
        markdown=True,
    )
    plan_task = Task(
        name="draft_execution_backlog",
        description=(
            "Transform the feature scan, bug scan, code quality scan, and process scan into an actionable upgrade backlog.\n"
            "Output JSON matching UpgradePlan.\n"
            "Rules:\n"
            "- Features use lane=feature, kind=FEATURE, require user confirmation, and use version_bump=major or minor.\n"
            "- Bugs use lane=bug, kind=BUG, no user confirmation, and use version_bump=patch.\n"
            "- Code quality improvements use lane=quality, kind=CODE_QUALITY, require user confirmation, default to version_bump=none, and focus on cleanup/refactor/reuse/deletion work.\n"
            "- Process improvements use lane=process, kind=PROCESS, cooldown_hours=24, and version_bump=none.\n"
            "- Every finding must carry exactly one stable module name. Prefer one of: Runtime, Self-Upgrade, CI, Doctor, Bootstrap, Workspace, GitHub-Project, Delivery, Proposal, Review, QA, CLI, Hub, Release, Requirements, Observability, Security.\n"
            "- Every feature, bug, or quality finding must include work_items. Each work item must be small, scoped, and suitable for a single coding agent.\n"
            "- Each work item must include owner_role, review_role, qa_role, allowed_paths, tests, acceptance, worktree_hint, and should stay inside the same module as the finding.\n"
            "- Quality work items should prefer deleting dead files, consolidating duplicate code, extracting shared logic, or narrowing oversized modules. Do not propose cosmetic-only cleanup.\n"
            "- Coding work items must be issue-scoped only; no extra optimization outside the listed paths.\n"
            "- 所有面向用户的自然语言字段必须使用简体中文，包括 title、summary、rationale、acceptance、work_items.title、work_items.summary。\n"
            "- 保留 role id、路径、命令、状态枚举、版本号、URL、worktree_hint 为原样。\n"
            "- Also include repo-level ci_actions and notes.\n"
        ),
        expected_output="A structured JSON upgrade plan.",
        agent=issue_drafter,
        context=[feature_task, bug_task, quality_task, process_task],
        output_json=UpgradePlan,
    )
    review_task = Task(
        name="review_delivery_plan",
        description=(
            "Review the draft upgrade plan.\n"
            "Reject large or fuzzy work items. Ensure every coding work item has clear path scope, task-linked commit discipline, and explicit downstream review/QA roles.\n"
            "Reject any finding that spans multiple modules or uses an unstable module name.\n"
            "For quality items, reject vague refactors or cleanup that is not backed by concrete evidence from the repository context.\n"
            "Keep all user-facing natural language fields in Simplified Chinese.\n"
            f"Keep no more than {int(max_findings)} findings in the final output."
        ),
        expected_output="A validated structured JSON upgrade plan ready for issue/task recording.",
        agent=review_agent,
        context=[feature_task, bug_task, quality_task, process_task, plan_task],
        output_json=UpgradePlan,
    )
    qa_task = Task(
        name="qa_acceptance_gate",
        description=(
            "Finalize the plan from a QA and release perspective.\n"
            "Make sure each work item has explicit tests and acceptance. Features and quality items must wait for user confirmation. Bugs can flow immediately.\n"
            "Preserve the single-module rule so downstream issue titles can follow [Type][Module] xxx.\n"
            "Keep all user-facing natural language fields in Simplified Chinese.\n"
            "No item should be closeable without review and QA acceptance."
        ),
        expected_output="A final structured JSON upgrade plan ready for runtime materialization.",
        agent=qa_agent,
        context=[feature_task, bug_task, quality_task, process_task, plan_task, review_task],
        output_json=UpgradePlan,
    )

    crew = Crew(
        agents=[product_manager, test_manager, issue_drafter, review_agent, qa_agent, process_analyst, code_quality_analyst],
        tasks=[feature_task, bug_task, quality_task, process_task, plan_task, review_task, qa_task],
        process=Process.sequential,
        verbose=verbose,
    )
    out = crew.kickoff()
    current_version = str(repo_context.get("current_version") or "0.1.0").strip() or "0.1.0"
    plan = _coerce_plan(out, max_findings=max_findings, repo_root=Path(str(repo_context.get("repo_root") or ".")), current_version=current_version)
    plan = plan.model_copy(update={"findings": [_localize_finding_to_zh(f) for f in (plan.findings or [])]})
    return plan, {
        "raw": str(out),
        "token_usage": getattr(out, "token_usage", None).model_dump() if getattr(out, "token_usage", None) else {},
        "task_outputs": [
            {
                "name": str(getattr(t, "name", "") or ""),
                "agent": str(getattr(t, "agent", "") or ""),
                "raw": str(getattr(t, "raw", "") or "")[:4000],
            }
            for t in (getattr(out, "tasks_output", None) or [])
        ],
    }


def _task_ledger_dir(project_id: str) -> Path:
    if str(project_id) == "teamos":
        return ledger_tasks_dir()
    workspace_store.ensure_project_scaffold(project_id)
    return workspace_store.ledger_tasks_dir(project_id)


def _compat_file_mirror_enabled() -> bool:
    return _env_truthy("TEAMOS_RUNTIME_FILE_MIRROR", "0")


def _is_self_upgrade_task_doc(doc: dict[str, Any]) -> bool:
    orchestration = doc.get("orchestration") or {}
    return isinstance(orchestration, dict) and str(orchestration.get("flow") or "").strip().lower() == "self_upgrade"


def _iter_self_upgrade_task_docs(*, project_id: str, target_id: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in improvement_store.list_delivery_tasks(project_id=str(project_id or ""), target_id=str(target_id or "")):
        if not isinstance(doc, dict) or not _is_self_upgrade_task_doc(doc):
            continue
        task_id = str(doc.get("id") or doc.get("task_id") or "").strip()
        if task_id:
            seen.add(task_id)
        out.append(doc)
    d = _task_ledger_dir(project_id)
    if not d.exists():
        return out
    for p in sorted(d.glob("*.yaml")):
        doc = _load_yaml(p)
        if not isinstance(doc, dict) or not _is_self_upgrade_task_doc(doc):
            continue
        task_id = str(doc.get("id") or doc.get("task_id") or p.stem).strip()
        if task_id in seen:
            continue
        out.append(doc)
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            doc = raw if isinstance(raw, dict) else {}
            if _is_self_upgrade_task_doc(doc):
                try:
                    improvement_store.upsert_delivery_task(doc)
                except Exception:
                    pass
            return doc
        except Exception:
            pass
    task_id = str(path.stem or "").strip()
    if task_id:
        doc = improvement_store.get_delivery_task(task_id)
        if isinstance(doc, dict):
            return doc
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        doc = raw if isinstance(raw, dict) else {}
        if _is_self_upgrade_task_doc(doc):
            try:
                improvement_store.upsert_delivery_task(doc)
            except Exception:
                pass
        return doc
    except Exception:
        return {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    if _is_self_upgrade_task_doc(payload or {}):
        try:
            improvement_store.upsert_delivery_task(dict(payload or {}))
        except Exception:
            pass
    if not _compat_file_mirror_enabled():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _task_title_for_finding(finding: UpgradeFinding) -> str:
    module = _normalize_module_name(
        str(finding.module or "").strip(),
        paths=list(finding.files or []),
        workstream_id=str(finding.workstream_id or ""),
        title=str(finding.title or ""),
        summary=str(finding.summary or ""),
        lane=str(finding.lane or ""),
    )
    return f"[{_issue_type_token(finding.lane)}][{module}] {finding.title}".strip()


def _issue_title_for_finding(repo_name: str, finding: UpgradeFinding) -> str:
    return _task_title_for_finding(finding)


def _finding_fingerprint(*, repo_locator: str, repo_root: Path, finding: UpgradeFinding) -> str:
    repo_root = repo_root.resolve()
    seed = "|".join(
        [
            str(repo_locator or ""),
            str(repo_root),
            str(finding.kind or "").upper(),
            str(finding.title or "").strip().lower(),
        ]
    )
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _proposal_id_for_finding(*, repo_locator: str, repo_root: Path, finding: UpgradeFinding) -> str:
    return f"su-{finding.lane}-{_finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding)}"


def _work_item_key(item: UpgradeWorkItem) -> str:
    seed = "|".join(
        [
            str(item.worktree_hint or "").strip(),
            str(item.owner_role or "").strip(),
            ",".join(sorted([str(x).strip() for x in (item.allowed_paths or []) if str(x).strip()])),
            ",".join(sorted([str(x).strip() for x in (item.tests or []) if str(x).strip()])),
        ]
    )
    if not seed.strip():
        seed = str(item.title or "").strip().lower()
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _task_title_for_work_item(finding: UpgradeFinding, item: UpgradeWorkItem) -> str:
    module = _normalize_module_name(
        str(item.module or finding.module or "").strip(),
        paths=list(item.allowed_paths or finding.files or []),
        workstream_id=str(item.workstream_id or finding.workstream_id or ""),
        title=str(item.title or finding.title or ""),
        summary=str(item.summary or finding.summary or ""),
        lane=str(finding.lane or ""),
    )
    return f"[{_issue_type_token(finding.lane)}][{module}] {item.title}".strip()


def _issue_title_for_work_item(repo_name: str, finding: UpgradeFinding, item: UpgradeWorkItem) -> str:
    return _task_title_for_work_item(finding, item)


def _proposal_module(doc: dict[str, Any]) -> str:
    return _normalize_module_name(
        str(doc.get("module") or "").strip(),
        paths=[str(x).strip() for x in (doc.get("files") or []) if str(x).strip()],
        workstream_id=str(doc.get("workstream_id") or ""),
        title=str(doc.get("title") or ""),
        summary=str(doc.get("summary") or ""),
        lane=str(doc.get("lane") or ""),
    )


def _proposal_issue_labels(doc: dict[str, Any]) -> list[str]:
    lane = str(doc.get("lane") or "feature").strip().lower() or "feature"
    module = _proposal_module(doc)
    labels = [
        "teamos",
        "source:self-upgrade",
        f"type:{lane if lane in ('feature', 'bug', 'process', 'quality') else 'feature'}",
        f"module:{_module_slug(module)}",
        "stage:proposal",
        _proposal_status_label(str(doc.get("status") or "")),
        _version_label(str(doc.get("version_bump") or "")),
    ]
    return sorted({str(x).strip() for x in labels if str(x).strip()})


def _task_issue_stage_label(doc: dict[str, Any]) -> str:
    execution = doc.get("self_upgrade_execution") or {}
    stage = str((execution if isinstance(execution, dict) else {}).get("stage") or "").strip().lower()
    status = str(doc.get("status") or "").strip().lower()
    if status in ("needs_clarification",):
        return "stage:needs-clarification"
    if stage in ("audit", "coding", "review", "qa", "docs", "release", "blocked", "closed", "merge_conflict", "needs_clarification"):
        return {"closed": "stage:done", "merge_conflict": "stage:merge-conflict"}.get(stage, f"stage:{stage}")
    if status in ("doing",):
        return "stage:coding"
    if status in ("test",):
        return "stage:qa"
    if status in ("release",):
        return "stage:release"
    if status in ("merge_conflict",):
        return "stage:merge-conflict"
    if status in ("blocked",):
        return "stage:blocked"
    if status in ("closed", "done"):
        return "stage:done"
    return "stage:queued"


def _task_issue_labels(*, doc: dict[str, Any], finding: UpgradeFinding, work_item: UpgradeWorkItem) -> list[str]:
    module = _normalize_module_name(
        str(work_item.module or finding.module or "").strip(),
        paths=list(work_item.allowed_paths or finding.files or []),
        workstream_id=str(work_item.workstream_id or finding.workstream_id or ""),
        title=str(work_item.title or finding.title or ""),
        summary=str(work_item.summary or finding.summary or ""),
        lane=str(finding.lane or ""),
    )
    lane = str(finding.lane or "bug").strip().lower() or "bug"
    labels = [
        "teamos",
        "source:self-upgrade",
        f"type:{lane if lane in ('feature', 'bug', 'process', 'quality') else 'bug'}",
        f"module:{_module_slug(module)}",
        _task_issue_stage_label(doc),
        _version_label(str(finding.version_bump or "")),
    ]
    milestone_doc = doc.get("self_upgrade_milestone") or {}
    if not isinstance(milestone_doc, dict):
        milestone_doc = {}
    milestone_title = ""
    if lane in ("feature", "bug"):
        milestone_title = str(milestone_doc.get("title") or _milestone_title_for_target_version(str(finding.target_version or ""))).strip()
    if milestone_title:
        labels.append(f"milestone:{_module_slug(milestone_title)}")
    return sorted({str(x).strip() for x in labels if str(x).strip()})


def _task_issue_audit_lines(doc: dict[str, Any], *, finding: UpgradeFinding) -> list[str]:
    audit = doc.get("self_upgrade_audit") or {}
    if not isinstance(audit, dict):
        audit = {}
    lane = str(audit.get("classification") or finding.lane or "").strip().lower() or str(finding.lane or "bug").strip().lower() or "bug"
    closure = str(audit.get("closure") or audit.get("status") or "pending").strip() or "pending"
    feedback = [str(x).strip() for x in (audit.get("feedback") or []) if str(x).strip()]
    lines = [
        f"- 审计角色: {role_display_zh(str(audit.get('audit_role') or ROLE_ISSUE_AUDIT_AGENT))} ({str(audit.get('audit_role') or ROLE_ISSUE_AUDIT_AGENT)})",
        f"- 当前状态: {str(audit.get('status') or 'pending')}",
        f"- 问题分类: {_issue_type_token(lane)}",
        f"- 闭环性: {closure}",
        f"- 值得进入开发: {'是' if bool(audit.get('worth_doing', True)) else '否'}",
        f"- 需要文档同步: {'是' if bool(audit.get('docs_required', False)) else '否'}",
    ]
    if str(audit.get("summary") or "").strip():
        lines.append(f"- 审计结论: {str(audit.get('summary') or '').strip()}")
    lines.extend([f"- 审计反馈: {item}" for item in feedback] or ["- 审计反馈: （待审计）"])
    return lines


def _task_issue_documentation_lines(doc: dict[str, Any], *, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> list[str]:
    policy = doc.get("documentation_policy") or {}
    if not isinstance(policy, dict):
        policy = {}
    if not policy:
        policy = _default_documentation_policy(finding=finding, work_item=work_item)
    allowed_paths = [str(x).strip() for x in (policy.get("allowed_paths") or []) if str(x).strip()]
    lines = [
        f"- 文档角色: {role_display_zh(str(policy.get('documentation_role') or ROLE_DOCUMENTATION_AGENT))} ({str(policy.get('documentation_role') or ROLE_DOCUMENTATION_AGENT)})",
        f"- 是否必需: {'是' if bool(policy.get('required')) else '否'}",
        f"- 当前状态: {str(policy.get('status') or ('pending' if bool(policy.get('required')) else 'not_required'))}",
        f"- 同步理由: {str(policy.get('rationale') or '(无)')}",
    ]
    if allowed_paths:
        lines.append(f"- 允许更新路径: {', '.join(allowed_paths)}")
    else:
        lines.append("- 允许更新路径: （未指定）")
    return lines


def _task_issue_milestone_lines(doc: dict[str, Any], *, finding: UpgradeFinding) -> list[str]:
    milestone = doc.get("self_upgrade_milestone") or {}
    if not isinstance(milestone, dict):
        milestone = {}
    milestone_title = ""
    if str(finding.lane or "").strip().lower() in ("feature", "bug"):
        milestone_title = str(milestone.get("title") or _milestone_title_for_target_version(str(finding.target_version or ""))).strip()
    lines = [
        f"- 里程碑角色: {role_display_zh(str(milestone.get('manager_role') or ROLE_MILESTONE_MANAGER))} ({str(milestone.get('manager_role') or ROLE_MILESTONE_MANAGER)})",
        f"- 发布线: {str(milestone.get('release_line') or _release_line_for_finding(finding) or '(无)')}",
        f"- 当前状态: {str(milestone.get('state') or 'draft')}",
        f"- 目标版本: {str(milestone.get('target_version') or finding.target_version or '')}",
        f"- GitHub Milestone: {milestone_title or '(不适用)'}",
    ]
    if int(milestone.get("github_milestone_number") or 0) > 0:
        lines[-1] = f"- GitHub Milestone: {milestone_title or '(不适用)'} (#{int(milestone.get('github_milestone_number') or 0)})"
    if str(milestone.get("release_issue_url") or "").strip():
        issue_number = int(milestone.get("release_issue_number") or 0)
        issue_text = f"#{issue_number}" if issue_number > 0 else str(milestone.get("release_issue_url") or "").strip()
        lines.append(f"- Release Issue: {issue_text} {str(milestone.get('release_issue_url') or '').strip()}".strip())
    lines.append(
        f"- 当前任务统计: total={int(milestone.get('total_items') or 0)}, open={int(milestone.get('open_items') or 0)}, blocked={int(milestone.get('blocked_items') or 0)}, done={int(milestone.get('done_items') or 0)}"
    )
    return lines


def _milestone_task_summary(*, project_id: str, milestone_id: str, extra_doc: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    total_items = 0
    open_items = 0
    blocked_items = 0
    done_items = 0
    workstreams: set[str] = set()
    links: list[str] = []
    tasks: list[dict[str, Any]] = []

    def _collect(doc: dict[str, Any]) -> None:
        nonlocal total_items, open_items, blocked_items, done_items
        if not isinstance(doc, dict):
            return
        orchestration = doc.get("orchestration") or {}
        if not isinstance(orchestration, dict) or str(orchestration.get("flow") or "").strip().lower() != "self_upgrade":
            return
        milestone = doc.get("self_upgrade_milestone") or {}
        if not isinstance(milestone, dict) or str(milestone.get("milestone_id") or "").strip() != milestone_id:
            return
        total_items += 1
        status = str(doc.get("status") or "").strip().lower()
        if status in ("done", "closed"):
            done_items += 1
        else:
            open_items += 1
        if status in ("blocked", "needs_clarification", "merge_conflict"):
            blocked_items += 1
        workstream = str(doc.get("workstream_id") or "").strip()
        if workstream:
            workstreams.add(workstream)
        issue_url = str((((doc.get("links") or {}) if isinstance(doc.get("links"), dict) else {}).get("issue")) or "").strip()
        if issue_url:
            links.append(issue_url)
        tasks.append(
            {
                "task_id": str(doc.get("task_id") or "").strip(),
                "title": str(doc.get("title") or "").strip(),
                "status": status,
                "issue_url": issue_url,
            }
        )
    for doc in _iter_self_upgrade_task_docs(project_id=project_id):
        _collect(doc)
    extra_task_id = ""
    if isinstance(extra_doc, dict):
        extra_task_id = str(extra_doc.get("task_id") or "").strip()
        if extra_task_id and extra_task_id not in {str(item.get("task_id") or "").strip() for item in tasks}:
            _collect(extra_doc)
    return {
        "total_items": total_items,
        "open_items": open_items,
        "blocked_items": blocked_items,
        "done_items": done_items,
        "workstreams": sorted(workstreams),
        "links": sorted({x for x in links if x}),
        "tasks": tasks,
    }


def _build_milestone_doc(
    *,
    project_id: str,
    repo_locator: str,
    finding: UpgradeFinding,
    work_item: UpgradeWorkItem,
    existing: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    lane = str(finding.lane or "").strip().lower()
    milestone_title = _milestone_title_for_target_version(str(finding.target_version or ""))
    if lane not in ("feature", "bug") or not milestone_title:
        return {}
    existing = dict(existing or {})
    milestone_id = str(existing.get("milestone_id") or _milestone_id_for_title(milestone_title)).strip()
    release_line = str(existing.get("release_line") or _release_line_for_finding(finding)).strip()
    start_date, target_date, due_on = _milestone_schedule(release_line)
    summary = _milestone_task_summary(project_id=project_id, milestone_id=milestone_id)
    state = str(existing.get("state") or "").strip()
    if state not in ("frozen", "released"):
        state = _milestone_state_from_metrics(
            total_items=int(summary.get("total_items") or 0),
            blocked_items=int(summary.get("blocked_items") or 0),
            done_items=int(summary.get("done_items") or 0),
        )
    objective = str(existing.get("objective") or "").strip() or f"交付 {milestone_title} 版本中的 {role_display_zh(work_item.owner_role)} 与相关验收工单。"
    links = sorted(
        {
            str(x).strip()
            for x in ([str(x).strip() for x in (existing.get("links") or [])] + [str(x).strip() for x in (summary.get("links") or [])])
            if str(x).strip()
        }
    )
    workstreams = sorted(
        {
            str(x).strip()
            for x in (
                list(existing.get("workstreams") or [])
                + list(summary.get("workstreams") or [])
                + [str(work_item.workstream_id or finding.workstream_id or "").strip()]
            )
            if str(x).strip()
        }
    )
    return {
        "milestone_id": milestone_id,
        "title": milestone_title,
        "start_date": str(existing.get("start_date") or start_date),
        "target_date": str(existing.get("target_date") or target_date),
        "workstreams": workstreams,
        "objective": objective,
        "links": links,
        "state": state or "draft",
        "release_line": release_line,
        "target_version": str(existing.get("target_version") or finding.target_version or "").strip(),
        "version_bump": str(existing.get("version_bump") or finding.version_bump or "").strip(),
        "repo_locator": str(existing.get("repo_locator") or repo_locator).strip(),
        "manager_role": str(existing.get("manager_role") or ROLE_MILESTONE_MANAGER).strip(),
        "github_milestone_number": int(existing.get("github_milestone_number") or 0),
        "github_milestone_due_on": str(existing.get("github_milestone_due_on") or due_on),
        "release_issue_number": int(existing.get("release_issue_number") or 0),
        "release_issue_url": str(existing.get("release_issue_url") or "").strip(),
        "total_items": int(summary.get("total_items") or 0),
        "open_items": int(summary.get("open_items") or 0),
        "blocked_items": int(summary.get("blocked_items") or 0),
        "done_items": int(summary.get("done_items") or 0),
        "updated_at": _utc_now_iso(),
    }


def _release_issue_body(*, project_id: str, milestone: dict[str, Any], summary: dict[str, Any]) -> str:
    marker = _release_issue_marker(project_id=project_id, milestone_id=str(milestone.get("milestone_id") or ""))
    tasks = list(summary.get("tasks") or [])
    lines = [
        marker,
        "# 版本发布跟踪",
        "",
        f"- 里程碑: {str(milestone.get('title') or '').strip()}",
        f"- 里程碑 ID: {str(milestone.get('milestone_id') or '').strip()}",
        f"- 管理角色: {role_display_zh(str(milestone.get('manager_role') or ROLE_MILESTONE_MANAGER))} ({str(milestone.get('manager_role') or ROLE_MILESTONE_MANAGER)})",
        f"- 发布线: {str(milestone.get('release_line') or '').strip() or '(无)'}",
        f"- 当前状态: {str(milestone.get('state') or 'draft')}",
        f"- 目标日期: {str(milestone.get('target_date') or '').strip() or '(未定)'}",
        "",
        "## 版本目标",
        "",
        str(milestone.get("objective") or "").strip() or "(无)",
        "",
        "## 当前统计",
        "",
        f"- 总任务数: {int(milestone.get('total_items') or 0)}",
        f"- 未完成: {int(milestone.get('open_items') or 0)}",
        f"- 阻塞: {int(milestone.get('blocked_items') or 0)}",
        f"- 已完成: {int(milestone.get('done_items') or 0)}",
        "",
        "## 当前包含任务",
        "",
    ]
    if tasks:
        for item in tasks[:20]:
            title = str(item.get("title") or item.get("task_id") or "(未命名任务)").strip()
            issue_url = str(item.get("issue_url") or "").strip()
            status = str(item.get("status") or "").strip() or "unknown"
            lines.append(f"- {title} [{status}] {issue_url}".strip())
    else:
        lines.append("- （当前还没有挂到该里程碑的任务）")
    lines.extend(["", "## 发布门禁", "", "- 所有 task issue 需要完成 coding/review/qa/docs/release 闭环。", "- blocked 或 needs_clarification 状态必须清零后才能进入发布。", ""])
    return "\n".join(lines)


def sync_milestone_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    finding, work_item = _finding_from_task_doc(doc)
    if finding is None or work_item is None:
        return {"ok": False, "reason": "missing_self_upgrade_finding"}
    project_id = str(doc.get("project_id") or "teamos").strip() or "teamos"
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    repo_locator = str(repo.get("locator") or "").strip()
    existing = doc.get("self_upgrade_milestone") or {}
    if not isinstance(existing, dict):
        existing = {}
    milestone = _build_milestone_doc(
        project_id=project_id,
        repo_locator=repo_locator,
        finding=finding,
        work_item=work_item,
        existing=existing,
    )
    if not milestone:
        return {"ok": False, "reason": "milestone_not_required"}
    summary = _milestone_task_summary(
        project_id=project_id,
        milestone_id=str(milestone.get("milestone_id") or ""),
        extra_doc={**doc, "self_upgrade_milestone": dict(milestone)},
    )
    milestone.update(
        {
            "total_items": int(summary.get("total_items") or 0),
            "open_items": int(summary.get("open_items") or 0),
            "blocked_items": int(summary.get("blocked_items") or 0),
            "done_items": int(summary.get("done_items") or 0),
            "workstreams": sorted({str(x).strip() for x in (milestone.get("workstreams") or []) + (summary.get("workstreams") or []) if str(x).strip()}),
            "links": sorted({str(x).strip() for x in (milestone.get("links") or []) + (summary.get("links") or []) if str(x).strip()}),
        }
    )
    milestone["state"] = str(existing.get("state") or milestone.get("state") or "").strip()
    if milestone["state"] not in ("frozen", "released"):
        milestone["state"] = _milestone_state_from_metrics(
            total_items=int(milestone.get("total_items") or 0),
            blocked_items=int(milestone.get("blocked_items") or 0),
            done_items=int(milestone.get("done_items") or 0),
        )
    if repo_locator:
        description = f"Team OS self-upgrade release milestone for {str(milestone.get('title') or '').strip()}."
        try:
            milestone_number = ensure_milestone(
                repo_locator,
                title=str(milestone.get("title") or "").strip(),
                description=description,
                due_on=str(milestone.get("github_milestone_due_on") or "").strip() or None,
            )
            if milestone_number > 0:
                milestone["github_milestone_number"] = milestone_number
        except (GitHubAuthError, GitHubIssuesBusError):
            pass
        marker = _release_issue_marker(project_id=project_id, milestone_id=str(milestone.get("milestone_id") or ""))
        title = _release_issue_title(milestone_title=str(milestone.get("title") or "").strip())
        labels = sorted(
            {
                "teamos",
                "source:self-upgrade",
                "type:process",
                "module:release",
                "stage:release",
                _version_label(str(milestone.get("version_bump") or "")),
                f"milestone:{_module_slug(str(milestone.get('title') or ''))}",
            }
        )
        body = _release_issue_body(project_id=project_id, milestone=milestone, summary=summary)
        try:
            issue = ensure_issue(
                repo_locator,
                title=title,
                body=body,
                allow_create=True,
                labels=labels,
                milestone=int(milestone.get("github_milestone_number") or 0) or None,
                marker=marker,
            )
            issue = update_issue(
                repo_locator,
                int(issue.number),
                title=title,
                body=body,
                labels=labels,
                state="open",
                milestone=int(milestone.get("github_milestone_number") or 0) or None,
            )
            milestone["release_issue_number"] = int(issue.number or 0)
            milestone["release_issue_url"] = str(issue.url or "").strip()
            milestone["links"] = sorted({str(x).strip() for x in (milestone.get("links") or []) + [str(issue.url or "").strip()] if str(x).strip()})
        except (GitHubAuthError, GitHubIssuesBusError):
            pass
    upsert_runtime_milestone(project_id, milestone)
    doc["self_upgrade_milestone"] = milestone
    return {"ok": True, "milestone": milestone}


def _task_issue_milestone_number(*, repo_locator: str, finding: UpgradeFinding, doc: Optional[dict[str, Any]] = None) -> Optional[int]:
    milestone = (doc or {}).get("self_upgrade_milestone") if isinstance(doc, dict) else None
    if isinstance(milestone, dict):
        num = int(milestone.get("github_milestone_number") or 0)
        if num > 0:
            return num
    lane = str(finding.lane or "").strip().lower()
    milestone_title = _milestone_title_for_target_version(str(finding.target_version or ""))
    if lane not in ("feature", "bug") or not milestone_title or not repo_locator:
        return None
    description = f"Team OS self-upgrade release milestone for {milestone_title}."
    try:
        return ensure_milestone(repo_locator, title=milestone_title, description=description)
    except (GitHubAuthError, GitHubIssuesBusError):
        return None


def list_proposals(*, target_id: str = "", project_id: str = "", lane: str = "", status: str = "") -> list[dict[str, Any]]:
    return improvement_store.list_proposals(target_id=str(target_id or "").strip(), project_id=str(project_id or "").strip(), lane=lane, status=status)


def decide_proposal(
    *,
    proposal_id: str,
    action: str,
    title: str = "",
    summary: str = "",
    version_bump: str = "",
) -> dict[str, Any]:
    pid = str(proposal_id or "").strip()
    if not pid:
        raise SelfUpgradeError("proposal_id is required")
    act = str(action or "").strip().lower()
    if act not in ("approve", "reject", "hold"):
        raise SelfUpgradeError("action must be one of: approve, reject, hold")
    doc = improvement_store.get_proposal(pid)
    if not isinstance(doc, dict):
        raise SelfUpgradeError(f"proposal not found: {pid}")
    now = _utc_now_iso()
    if title:
        doc["title"] = str(title).strip()
    if summary:
        doc["summary"] = str(summary).strip()
    if version_bump:
        vb = str(version_bump).strip().lower()
        if vb not in ("major", "minor", "patch", "none"):
            raise SelfUpgradeError("version_bump must be one of: major, minor, patch, none")
        doc["version_bump"] = vb
        lane = str(doc.get("lane") or "").strip().lower() or "feature"
        current_version = str(doc.get("current_version") or "0.1.0").strip() or "0.1.0"
        doc["target_version"] = current_version if vb == "none" else _bump_version(current_version, vb)
        doc["baseline_action"] = _lane_default_baseline_action(lane, vb)
    if act == "approve":
        doc["status"] = "APPROVED"
        doc["approved_at"] = now
    elif act == "reject":
        doc["status"] = "REJECTED"
        doc["rejected_at"] = now
    else:
        doc["status"] = "HOLD"
    doc["updated_at"] = now
    doc["proposal_id"] = pid
    improvement_store.upsert_proposal(doc)
    return {"proposal_id": pid, **doc}


def _update_proposal_record(
    proposal_id: str,
    *,
    title: str = "",
    summary: str = "",
    version_bump: str = "",
    status: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    pid = str(proposal_id or "").strip()
    if not pid:
        raise SelfUpgradeError("proposal_id is required")
    doc = improvement_store.get_proposal(pid)
    if not isinstance(doc, dict):
        raise SelfUpgradeError(f"proposal not found: {pid}")
    now = _utc_now_iso()
    if title:
        doc["title"] = str(title).strip()
    if summary:
        doc["summary"] = str(summary).strip()
    if version_bump:
        vb = str(version_bump).strip().lower()
        if vb not in ("major", "minor", "patch", "none"):
            raise SelfUpgradeError("version_bump must be one of: major, minor, patch, none")
        lane = str(doc.get("lane") or "").strip().lower() or "feature"
        current_version = str(doc.get("current_version") or "0.1.0").strip() or "0.1.0"
        doc["version_bump"] = vb
        doc["target_version"] = current_version if vb == "none" else _bump_version(current_version, vb)
        doc["baseline_action"] = _lane_default_baseline_action(lane, vb)
    if status:
        doc["status"] = str(status).strip().upper()
    if isinstance(extra, dict):
        doc.update(extra)
    doc["module"] = _normalize_module_name(
        str(doc.get("module") or "").strip(),
        paths=[str(x).strip() for x in (doc.get("files") or []) if str(x).strip()],
        workstream_id=str(doc.get("workstream_id") or ""),
        title=str(doc.get("title") or ""),
        summary=str(doc.get("summary") or ""),
        lane=str(doc.get("lane") or ""),
    )
    doc["updated_at"] = now
    doc["proposal_id"] = pid
    improvement_store.upsert_proposal(doc)
    return {"proposal_id": pid, **doc}


def _proposal_issue_title(doc: dict[str, Any]) -> str:
    title = str(doc.get("title") or "未命名改进提案").strip()
    module = _proposal_module(doc)
    lane = str(doc.get("lane") or "feature").strip().lower() or "feature"
    return f"[{_issue_type_token(lane)}][{module}] {title}".strip()


def _proposal_issue_body(doc: dict[str, Any]) -> str:
    module = _proposal_module(doc)
    lane = str(doc.get("lane") or "feature").strip().lower() or "feature"
    milestone_title = ""
    if lane in ("feature", "bug") and str(doc.get("status") or "").strip().upper() == "MATERIALIZED":
        milestone_title = _milestone_title_for_target_version(str(doc.get("target_version") or ""))
    lines = [
        _proposal_issue_marker(doc),
        "# 改进提案讨论",
        "",
        f"- 提案 ID: {doc.get('proposal_id') or ''}",
        f"- 类型: {_issue_type_token(lane)}",
        f"- Module: {module}",
        f"- 仓库定位: {doc.get('repo_locator') or ''}",
        f"- 当前状态: {doc.get('status') or ''}",
        f"- 版本变更: {doc.get('version_bump') or ''}",
        f"- 目标版本: {doc.get('target_version') or ''}",
        f"- 目标里程碑: {milestone_title or '(待批准后分配)'}",
        f"- 冷静期截止: {doc.get('cooldown_until') or ''}",
        "",
        "## 概要",
        "",
        _normalize_issue_text(str(doc.get("summary") or "").strip()),
        "",
        "## 背景与原因",
        "",
        _normalize_issue_text(str(doc.get("rationale") or "").strip()),
        "",
        "## 拆分工作项",
        "",
    ]
    work_items = list(doc.get("work_items") or [])
    if work_items:
        for raw in work_items:
            item = raw if isinstance(raw, dict) else {}
            lines.append(f"- {str(item.get('title') or '').strip() or '(未命名工作项)'} [{role_display_zh(_normalize_owner_role(str(item.get('owner_role') or '').strip(), str(doc.get('lane') or 'feature')))}]")
    else:
        lines.append("- （无）")
    lines.extend(
        [
            "",
            "## 范围约束",
            "",
            "- 这是 proposal discussion issue，不直接进入编码执行。",
            "- 只有在你明确确认后，Team OS 才会拆分 execution work items 并分配 milestone。",
            "- 开发 issue 会单独创建，并遵守 [Type][Module] xxx 命名与单模块约束。",
            "",
            "## 如何回复",
            "",
            "- 直接在这个 issue 里提问即可，Team OS 的需求答复 Agent 会回复并调整提案。",
            "- 如果确认进入开发，请回复 `/approve` 或 `确认`。",
            "- 如果要暂缓，请回复 `/hold` 或 `暂缓`。",
            "- 如果决定不做，请回复 `/reject` 或 `不做`。",
            "",
        ]
    )
    return "\n".join(lines)


def _discussion_issue_number(doc: dict[str, Any]) -> int:
    try:
        return int(doc.get("discussion_issue_number") or 0)
    except Exception:
        return 0


def _ensure_proposal_discussion_issue(proposal: dict[str, Any]) -> dict[str, Any]:
    proposal = _localize_proposal_doc_to_zh(proposal)
    proposal = _update_proposal_record(
        str(proposal.get("proposal_id") or ""),
        title=str(proposal.get("title") or "").strip(),
        summary=str(proposal.get("summary") or "").strip(),
        extra={
            "module": _proposal_module(proposal),
            "rationale": str(proposal.get("rationale") or "").strip(),
            "work_items": list(proposal.get("work_items") or []),
        },
    )
    repo_locator = str(proposal.get("repo_locator") or "").strip()
    if not repo_locator:
        return dict(proposal)
    issue_number = _discussion_issue_number(proposal)
    title = _proposal_issue_title(proposal)
    body = _proposal_issue_body(proposal)
    labels = _proposal_issue_labels(proposal)
    marker = _proposal_issue_marker(proposal)
    try:
        if issue_number > 0:
            issue = update_issue(repo_locator, issue_number, title=title, body=body, labels=labels, state="open", milestone=None)
        else:
            issue = ensure_issue(repo_locator, title=title, body=body, allow_create=True, labels=labels, marker=marker)
            issue = update_issue(repo_locator, issue.number, title=title, body=body, labels=labels, state="open", milestone=None)
    except (GitHubAuthError, GitHubIssuesBusError) as e:
        return _update_proposal_record(
            str(proposal.get("proposal_id") or ""),
            extra={
                "discussion_error": str(e)[:500],
                "discussion_synced_at": _utc_now_iso(),
            },
        )
    return _update_proposal_record(
        str(proposal.get("proposal_id") or ""),
        extra={
            "discussion_issue_number": int(issue.number),
            "discussion_issue_url": str(issue.url or ""),
            "discussion_issue_title": str(issue.title or title),
            "discussion_error": "",
            "discussion_synced_at": _utc_now_iso(),
        },
    )


def _comment_is_user_comment(comment: Any) -> bool:
    body = str(getattr(comment, "body", "") or "")
    login = str(getattr(comment, "user_login", "") or "").strip().lower()
    if "<!-- teamos:" in body:
        return False
    if login.endswith("[bot]"):
        return False
    return True


def _proposal_action_from_comment_text(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    approve_hits = ("/approve", "/confirm", "确认", "同意", "开始开发", "可以开发", "go ahead")
    reject_hits = ("/reject", "不做", "取消", "放弃", "reject")
    hold_hits = ("/hold", "暂缓", "等等", "稍后", "hold")
    if any(token in raw for token in approve_hits):
        return "approve"
    if any(token in raw for token in reject_hits):
        return "reject"
    if any(token in raw for token in hold_hits):
        return "hold"
    return ""


def _discussion_fallback_reply(*, proposal: dict[str, Any], comments_text: str, explicit_action: str) -> ProposalDiscussionResponse:
    title = str(proposal.get("title") or "").strip()
    if explicit_action == "approve":
        body = f"已记录你的确认，proposal `{title}` 已进入 APPROVED。冷静期到达后会进入开发拆解。"
        return ProposalDiscussionResponse(reply_body=body, action="approve")
    if explicit_action == "reject":
        body = f"已记录你的决定，proposal `{title}` 已标记为 REJECTED，不会继续进入开发。"
        return ProposalDiscussionResponse(reply_body=body, action="reject")
    if explicit_action == "hold":
        body = f"已记录你的决定，proposal `{title}` 已标记为 HOLD，等待后续回复再继续。"
        return ProposalDiscussionResponse(reply_body=body, action="hold")
    body = (
        "已记录你的反馈。当前 proposal 仍保持待确认状态。\n\n"
        "如果你确认要进入开发，请回复 `/approve`。\n"
        "如果要暂缓，请回复 `/hold`。\n"
        "如果不做，请回复 `/reject`。"
    )
    if comments_text:
        body += "\n\n已捕获的最新反馈将用于下一轮 proposal 调整。"
    return ProposalDiscussionResponse(reply_body=body, action="pending")


def kickoff_proposal_discussion(*, proposal: dict[str, Any], comments: list[Any], verbose: bool = False) -> ProposalDiscussionResponse:
    crewai_runtime.require_crewai_importable()
    from crewai import Crew, Process, Task

    llm = _crewai_llm()
    payload = {
        "proposal": proposal,
        "new_comments": [
            {
                "id": int(getattr(c, "id", 0) or 0),
                "user_login": str(getattr(c, "user_login", "") or ""),
                "body": str(getattr(c, "body", "") or ""),
                "created_at": str(getattr(c, "created_at", "") or ""),
            }
            for c in comments
        ],
    }
    agent = crewai_agent_factory.build_crewai_agent(role_id=ROLE_ISSUE_DISCUSSION_AGENT, llm=llm, verbose=verbose)
    task = Task(
        name="reply_to_improvement_proposal_discussion",
        description=(
            "Read the proposal and the latest user comments.\n"
            "Return JSON matching ProposalDiscussionResponse.\n"
            "Rules:\n"
            "- If the user is only asking questions or suggesting changes, keep action=pending or hold.\n"
            "- Only set action=approve when the user explicitly confirms the proposal should proceed.\n"
            "- You may refine title, summary, version_bump, or module if the user feedback clearly changes the scope.\n"
            "- module must stay a single stable value such as Runtime, Self-Upgrade, CI, Doctor, Bootstrap, Workspace, GitHub-Project, Delivery, Proposal, Review, QA, CLI, Hub, Release, Requirements, Observability, Security.\n"
            "- Keep the reply concise and directly answer the user's latest questions.\n"
            "- 所有 reply_body、title、summary 必须使用简体中文。\n\n"
            f"Payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        ),
        expected_output="A structured JSON discussion reply.",
        agent=agent,
        output_json=ProposalDiscussionResponse,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=verbose)
    out = crew.kickoff()
    if hasattr(out, "to_dict"):
        return ProposalDiscussionResponse.model_validate(out.to_dict())
    if hasattr(out, "json_dict"):
        return ProposalDiscussionResponse.model_validate(getattr(out, "json_dict"))
    text = str(out or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise SelfUpgradeError("CrewAI returned no structured discussion reply")
    return ProposalDiscussionResponse.model_validate(json.loads(match.group(0)))


def reconcile_feature_discussions(*, db=None, actor: str = "self_upgrade_discussion_loop", verbose: bool = False) -> dict[str, Any]:
    proposals = [p for p in list_proposals() if str(p.get("lane") or "").strip().lower() in ("feature", "quality")]
    stats = {"scanned": 0, "updated": 0, "replied": 0, "errors": 0}
    for proposal in proposals:
        status = str(proposal.get("status") or "").strip().upper()
        if status in ("REJECTED", "MATERIALIZED"):
            continue
        stats["scanned"] += 1
        try:
            proposal = _ensure_proposal_discussion_issue(proposal)
            issue_number = _discussion_issue_number(proposal)
            repo_locator = str(proposal.get("repo_locator") or "").strip()
            if issue_number <= 0 or not repo_locator:
                continue
            last_seen = int(proposal.get("discussion_last_comment_id") or 0)
            comments = list_issue_comments(repo_locator, issue_number)
            new_comments = [c for c in comments if int(getattr(c, "id", 0) or 0) > last_seen and _comment_is_user_comment(c)]
            if not new_comments:
                continue
            latest_comment_id = max(int(getattr(c, "id", 0) or 0) for c in new_comments)
            comments_text = "\n\n".join([str(getattr(c, "body", "") or "").strip() for c in new_comments if str(getattr(c, "body", "") or "").strip()])
            explicit_action = _proposal_action_from_comment_text(comments_text)
            try:
                reply = kickoff_proposal_discussion(proposal=proposal, comments=new_comments, verbose=verbose)
            except Exception:
                reply = _discussion_fallback_reply(proposal=proposal, comments_text=comments_text, explicit_action=explicit_action)
            reply = _localize_discussion_response_to_zh(reply)
            action = explicit_action or str(reply.action or "").strip().lower()
            if action in ("approve", "reject", "hold"):
                proposal = decide_proposal(
                    proposal_id=str(proposal.get("proposal_id") or ""),
                    action=action,
                    title=str(reply.title or "").strip(),
                    summary=str(reply.summary or "").strip(),
                    version_bump=str(reply.version_bump or "").strip(),
                )
                if str(reply.module or "").strip():
                    proposal = _update_proposal_record(
                        str(proposal.get("proposal_id") or ""),
                        extra={"module": str(reply.module or "").strip()},
                    )
            else:
                proposal = _update_proposal_record(
                    str(proposal.get("proposal_id") or ""),
                    title=str(reply.title or "").strip(),
                    summary=str(reply.summary or "").strip(),
                    version_bump=str(reply.version_bump or "").strip(),
                    extra={"status": "PENDING_CONFIRMATION", "module": str(reply.module or "").strip()},
                )
            proposal = _ensure_proposal_discussion_issue(proposal)
            marker = f"<!-- teamos:proposal-reply:{str(proposal.get('proposal_id') or '').strip()}:{latest_comment_id} -->"
            upsert_comment_with_marker(
                repo_locator,
                issue_number,
                marker=marker,
                body=marker + "\n" + str(reply.reply_body or "").strip() + "\n",
                allow_create=True,
            )
            proposal = _update_proposal_record(
                str(proposal.get("proposal_id") or ""),
                extra={
                    "discussion_last_comment_id": latest_comment_id,
                    "discussion_last_user_comment_at": max([str(getattr(c, "updated_at", "") or getattr(c, "created_at", "") or "") for c in new_comments], default=""),
                    "discussion_reply_updated_at": _utc_now_iso(),
                    "awaiting_user_reply": False if action in ("approve", "reject") else True,
                },
            )
            stats["updated"] += 1
            stats["replied"] += 1
            if db is not None:
                try:
                    db.add_event(
                        event_type="SELF_UPGRADE_PROPOSAL_DISCUSSION_UPDATED",
                        actor=actor,
                        project_id=str(proposal.get("project_id") or "teamos"),
                        workstream_id=str(proposal.get("workstream_id") or "general"),
                        payload={
                            "proposal_id": proposal.get("proposal_id"),
                            "discussion_issue_url": proposal.get("discussion_issue_url") or "",
                            "status": proposal.get("status") or "",
                            "last_comment_id": latest_comment_id,
                        },
                    )
                except Exception:
                    pass
        except Exception as e:
            stats["errors"] += 1
            if db is not None:
                try:
                    db.add_event(
                        event_type="SELF_UPGRADE_PROPOSAL_DISCUSSION_FAILED",
                        actor=actor,
                        project_id=str(proposal.get("project_id") or "teamos"),
                        workstream_id=str(proposal.get("workstream_id") or "general"),
                        payload={"proposal_id": proposal.get("proposal_id"), "error": str(e)[:300]},
                    )
                except Exception:
                    pass
    return stats


def _upsert_proposal(
    *,
    target_id: str,
    repo_root: Path,
    repo_locator: str,
    project_id: str,
    finding: UpgradeFinding,
    current_version: str,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    finding = _localize_finding_to_zh(finding)
    workflow = crewai_workflow_registry.workflow_for_lane(finding.lane)
    if finding.work_items:
        finding = finding.model_copy(
            update={
                "work_items": [
                    w.model_copy(
                        update={
                            "module": _normalize_module_name(
                                str(w.module or finding.module or "").strip(),
                                paths=list(w.allowed_paths or finding.files or []),
                                workstream_id=str(w.workstream_id or finding.workstream_id or ""),
                                title=str(w.title or finding.title or ""),
                                summary=str(w.summary or finding.summary or ""),
                                lane=str(finding.lane or ""),
                            ),
                            "owner_role": _normalize_owner_role(w.owner_role, finding.lane),
                            "review_role": _normalize_review_role(w.review_role),
                            "qa_role": _normalize_qa_role(w.qa_role),
                        }
                    )
                    for w in finding.work_items
                ]
            }
        )
    proposal_id = _proposal_id_for_finding(repo_locator=repo_locator, repo_root=repo_root, finding=finding)
    existing = improvement_store.get_proposal(proposal_id) or {}
    now = _utc_now_iso()
    status = str(existing.get("status") or "").strip().upper()
    if not status:
        status = "PENDING_CONFIRMATION" if finding.requires_user_confirmation else "COLLECTING"
    cooldown_hours = max(0, int(finding.cooldown_hours or 0))
    cooldown_until = str(existing.get("cooldown_until") or "").strip()
    if not cooldown_until and cooldown_hours > 0:
        import datetime as _dt

        cooldown_until = (
            _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=cooldown_hours)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    doc = {
        "proposal_id": proposal_id,
        "workflow_id": str(existing.get("workflow_id") or workflow.workflow_id),
        "target_id": str(target_id or "").strip(),
        "lane": finding.lane,
        "kind": finding.kind,
        "module": str(existing.get("module") or finding.module or _normalize_module_name(paths=list(finding.files or []), workstream_id=finding.workstream_id, title=finding.title, summary=finding.summary, lane=finding.lane)),
        "title": str(existing.get("title") or finding.title),
        "summary": str(existing.get("summary") or finding.summary),
        "rationale": str(existing.get("rationale") or finding.rationale),
        "impact": str(existing.get("impact") or finding.impact),
        "workstream_id": str(existing.get("workstream_id") or finding.workstream_id or "general"),
        "project_id": str(existing.get("project_id") or project_id or "teamos"),
        "repo_root": str(repo_root),
        "repo_locator": repo_locator,
        "status": status,
        "requires_user_confirmation": bool(existing.get("requires_user_confirmation", finding.requires_user_confirmation)),
        "cooldown_hours": cooldown_hours,
        "cooldown_until": cooldown_until,
        "current_version": str(existing.get("current_version") or current_version or "0.1.0"),
        "version_bump": str(existing.get("version_bump") or finding.version_bump or _lane_default_version_bump(finding.lane)),
        "target_version": str(existing.get("target_version") or finding.target_version or _bump_version(current_version, finding.version_bump)),
        "baseline_action": str(existing.get("baseline_action") or finding.baseline_action or _lane_default_baseline_action(finding.lane, finding.version_bump)),
        "work_items": [w.model_dump() for w in (finding.work_items or [])],
        "files": list(finding.files or []),
        "tests": list(finding.tests or []),
        "acceptance": list(finding.acceptance or []),
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "approved_at": str(existing.get("approved_at") or ""),
        "rejected_at": str(existing.get("rejected_at") or ""),
        "materialized_at": str(existing.get("materialized_at") or ""),
        "discussion_issue_number": int(existing.get("discussion_issue_number") or 0),
        "discussion_issue_url": str(existing.get("discussion_issue_url") or ""),
        "discussion_issue_title": str(existing.get("discussion_issue_title") or ""),
        "discussion_last_comment_id": int(existing.get("discussion_last_comment_id") or 0),
        "discussion_last_user_comment_at": str(existing.get("discussion_last_user_comment_at") or ""),
        "discussion_reply_updated_at": str(existing.get("discussion_reply_updated_at") or ""),
        "discussion_synced_at": str(existing.get("discussion_synced_at") or ""),
        "discussion_error": str(existing.get("discussion_error") or ""),
        "awaiting_user_reply": bool(existing.get("awaiting_user_reply", True)),
        "finding": finding.model_dump(),
    }
    improvement_store.upsert_proposal(doc)
    return {"proposal_id": proposal_id, **doc}


def _proposal_due(doc: dict[str, Any]) -> bool:
    cooldown_until = str(doc.get("cooldown_until") or "").strip()
    if not cooldown_until:
        return True
    try:
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        hold = _dt.datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
        return now >= hold
    except Exception:
        return True


def _find_existing_task(*, project_id: str, title: str, repo_locator: str, repo_root: Path, finding_fingerprint: str = "", work_item_key: str = "") -> Optional[dict[str, Any]]:
    for doc in improvement_store.list_delivery_tasks(project_id=project_id):
        orchestration = doc.get("orchestration") or {}
        if not isinstance(orchestration, dict):
            orchestration = {}
        if (
            finding_fingerprint
            and work_item_key
            and str(orchestration.get("finding_fingerprint") or "").strip() == str(finding_fingerprint).strip()
            and str(orchestration.get("work_item_key") or "").strip() == str(work_item_key).strip()
        ):
            ledger_path = str(((doc.get("artifacts") or {}) if isinstance(doc.get("artifacts"), dict) else {}).get("ledger_path") or "").strip()
            return {"task_id": str(doc.get("id") or "").strip(), "ledger_path": ledger_path, "doc": doc}
        if str(doc.get("title") or "").strip() != title:
            continue
        repo = doc.get("repo") or {}
        if not isinstance(repo, dict):
            repo = {}
        locator_matches = str(repo.get("locator") or "").strip() == str(repo_locator or "").strip()
        workdir_matches = str(repo.get("workdir") or "").strip() == str(repo_root)
        if locator_matches or workdir_matches:
            ledger_path = str(((doc.get("artifacts") or {}) if isinstance(doc.get("artifacts"), dict) else {}).get("ledger_path") or "").strip()
            return {"task_id": str(doc.get("id") or "").strip(), "ledger_path": ledger_path, "doc": doc}
    d = _task_ledger_dir(project_id)
    if not d.exists():
        return None
    for p in sorted(d.glob("*.yaml")):
        doc = _load_yaml(p)
        orchestration = doc.get("orchestration") or {}
        if not isinstance(orchestration, dict):
            orchestration = {}
        if (
            finding_fingerprint
            and work_item_key
            and str(orchestration.get("finding_fingerprint") or "").strip() == str(finding_fingerprint).strip()
            and str(orchestration.get("work_item_key") or "").strip() == str(work_item_key).strip()
        ):
            artifacts = doc.get("artifacts") or {}
            if not isinstance(artifacts, dict):
                artifacts = {}
            artifacts["ledger_path"] = str(p)
            doc["artifacts"] = artifacts
            improvement_store.upsert_delivery_task(doc)
            return {"task_id": str(doc.get("id") or "").strip(), "ledger_path": str(p), "doc": doc}
        if str(doc.get("title") or "").strip() != title:
            continue
        repo = doc.get("repo") or {}
        if not isinstance(repo, dict):
            repo = {}
        locator_matches = str(repo.get("locator") or "").strip() == str(repo_locator or "").strip()
        workdir_matches = str(repo.get("workdir") or "").strip() == str(repo_root)
        if locator_matches or workdir_matches:
            artifacts = doc.get("artifacts") or {}
            if not isinstance(artifacts, dict):
                artifacts = {}
            artifacts["ledger_path"] = str(p)
            doc["artifacts"] = artifacts
            improvement_store.upsert_delivery_task(doc)
            return {"task_id": str(doc.get("id") or "").strip(), "ledger_path": str(p), "doc": doc}
    return None


def _ensure_task_record(
    *,
    target_id: str,
    repo_root: Path,
    repo_locator: str,
    panel_project_id: str,
    finding: UpgradeFinding,
    work_item: UpgradeWorkItem,
    issue: _IssueRecord,
    proposal_id: str = "",
) -> dict[str, Any]:
    finding = _localize_finding_to_zh(finding)
    matched_work_item = next((w for w in (finding.work_items or []) if str(w.worktree_hint or "") == str(work_item.worktree_hint or "") or str(w.title or "") == str(work_item.title or "")), None)
    if matched_work_item is not None:
        work_item = matched_work_item
    work_item = work_item.model_copy(
        update={
            "owner_role": _normalize_owner_role(work_item.owner_role, finding.lane),
            "review_role": _normalize_review_role(work_item.review_role),
            "qa_role": _normalize_qa_role(work_item.qa_role),
        }
    )
    title = _task_title_for_work_item(finding, work_item)
    work_item_key = _work_item_key(work_item)
    existing = _find_existing_task(
        project_id=panel_project_id,
        title=title,
        repo_locator=repo_locator,
        repo_root=repo_root,
        finding_fingerprint=_finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding),
        work_item_key=work_item_key,
    )
    if existing:
        task_id = str(existing.get("task_id") or "").strip()
        ledger_path = Path(str(existing.get("ledger_path") or ""))
        doc = existing.get("doc") or {}
    else:
        scope = "teamos" if panel_project_id == "teamos" else f"project:{panel_project_id}"
        delegated = crew_tools.run_task_create_pipeline(
            repo_root=team_os_root(),
            workspace_root=crew_tools.workspace_root(),
            scope=scope,
            title=title,
            workstreams=[finding.workstream_id or "general"],
            mode="upgrade",
            dry_run=False,
        )
        created = delegated.get("result") or {}
        task_id = str(created.get("task_id") or "").strip()
        ledger_path = Path(str(created.get("ledger_path") or "")).resolve()
        doc = _load_yaml(ledger_path)

    if not task_id or not ledger_path:
        raise SelfUpgradeError(f"failed to materialize task record for finding={finding.title}")

    existing_execution = doc.get("self_upgrade_execution") or {}
    if not isinstance(existing_execution, dict):
        existing_execution = {}
    normalized_worktree_hint = _normalize_worktree_hint(
        repo_root=repo_root,
        lane=finding.lane,
        title=work_item.title or finding.title,
        raw_hint=str(existing_execution.get("worktree_path") or work_item.worktree_hint or ""),
    )
    normalized_module = _normalize_module_name(
        str(work_item.module or finding.module or "").strip(),
        paths=list(work_item.allowed_paths or finding.files or []),
        workstream_id=str(work_item.workstream_id or finding.workstream_id or ""),
        title=str(work_item.title or finding.title or ""),
        summary=str(work_item.summary or finding.summary or ""),
        lane=str(finding.lane or ""),
    )
    work_item = work_item.model_copy(update={"worktree_hint": normalized_worktree_hint, "module": normalized_module})
    finding = finding.model_copy(update={"module": _normalize_module_name(str(finding.module or normalized_module), paths=list(finding.files or work_item.allowed_paths or []), workstream_id=str(finding.workstream_id or ""), title=str(finding.title or ""), summary=str(finding.summary or ""), lane=str(finding.lane or ""))})
    existing_audit = doc.get("self_upgrade_audit") or {}
    if not isinstance(existing_audit, dict):
        existing_audit = {}
    default_docs = _default_documentation_policy(finding=finding, work_item=work_item)
    existing_docs = doc.get("documentation_policy") or {}
    if not isinstance(existing_docs, dict):
        existing_docs = {}
    existing_milestone = doc.get("self_upgrade_milestone") or {}
    if not isinstance(existing_milestone, dict):
        existing_milestone = {}
    documentation_policy = {
        "required": bool(existing_docs.get("required", default_docs["required"])),
        "status": str(existing_docs.get("status") or default_docs["status"]),
        "allowed_paths": [str(x).strip() for x in (existing_docs.get("allowed_paths") or default_docs["allowed_paths"]) if str(x).strip()],
        "rationale": str(existing_docs.get("rationale") or default_docs["rationale"]),
        "documentation_role": str(existing_docs.get("documentation_role") or default_docs["documentation_role"] or ROLE_DOCUMENTATION_AGENT),
        "updated_at": str(existing_docs.get("updated_at") or _utc_now_iso()),
        "completed_at": str(existing_docs.get("completed_at") or ""),
        "summary": str(existing_docs.get("summary") or ""),
        "changed_files": [str(x).strip() for x in (existing_docs.get("changed_files") or []) if str(x).strip()],
        "followups": [str(x).strip() for x in (existing_docs.get("followups") or []) if str(x).strip()],
    }
    audit_doc = {
        "status": str(existing_audit.get("status") or "pending"),
        "classification": str(existing_audit.get("classification") or finding.lane),
        "module": str(existing_audit.get("module") or normalized_module),
        "worth_doing": bool(existing_audit.get("worth_doing", True)),
        "closure": str(existing_audit.get("closure") or "pending"),
        "docs_required": bool(existing_audit.get("docs_required", documentation_policy["required"])),
        "summary": str(existing_audit.get("summary") or ""),
        "feedback": [str(x).strip() for x in (existing_audit.get("feedback") or []) if str(x).strip()],
        "audit_role": str(existing_audit.get("audit_role") or ROLE_ISSUE_AUDIT_AGENT),
        "updated_at": str(existing_audit.get("updated_at") or _utc_now_iso()),
        "approved_at": str(existing_audit.get("approved_at") or ""),
        "issue_title_snapshot": str(existing_audit.get("issue_title_snapshot") or ""),
    }
    milestone_doc = _build_milestone_doc(
        project_id=panel_project_id,
        repo_locator=repo_locator,
        finding=finding,
        work_item=work_item,
        existing=existing_milestone,
    )

    repo_doc = doc.get("repo") or {}
    if not isinstance(repo_doc, dict):
        repo_doc = {}
    repo_doc.update(
        {
            "locator": repo_locator,
            "workdir": str(repo_root),
            "branch": collect_repo_context(repo_root=repo_root, explicit_repo_locator=repo_locator).get("current_branch") or "",
            "mode": "upgrade",
        }
    )
    links = doc.get("links") or {}
    if not isinstance(links, dict):
        links = {}
    links["issue"] = issue.url
    links["repo"] = repo_locator
    doc["repo"] = repo_doc
    doc["links"] = links
    target_doc = improvement_store.ensure_target(project_id=panel_project_id, target_id=target_id, repo_path=str(repo_root), repo_locator=repo_locator)
    doc["target"] = {
        "target_id": str(target_doc.get("target_id") or target_id or ""),
        "display_name": str(target_doc.get("display_name") or repo_locator or repo_root.name),
    }
    artifacts = doc.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        artifacts = {}
    artifacts["ledger_path"] = str(ledger_path)
    doc["artifacts"] = artifacts
    doc["status"] = "todo"
    doc["workstream_id"] = work_item.workstream_id or finding.workstream_id or str(doc.get("workstream_id") or "general")
    doc["updated_at"] = _utc_now_iso()
    doc["owners"] = [work_item.owner_role]
    doc["owner_role"] = work_item.owner_role
    doc["roles_involved"] = [
        ROLE_ISSUE_AUDIT_AGENT,
        ROLE_MILESTONE_MANAGER,
        work_item.owner_role,
        work_item.review_role,
        work_item.qa_role,
        str(documentation_policy.get("documentation_role") or ROLE_DOCUMENTATION_AGENT),
    ]
    doc["need_pm_decision"] = False
    doc["orchestration"] = {
        "engine": "crewai",
        "flow": "self_upgrade",
        "finding_kind": finding.kind,
        "finding_lane": finding.lane,
        "finding_fingerprint": _finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding),
        "work_item_key": work_item_key,
        "proposal_id": proposal_id,
    }
    doc["workflows"] = ["SelfUpgrade"]
    doc["self_upgrade"] = {
        "kind": finding.kind,
        "lane": finding.lane,
        "module": finding.module,
        "summary": finding.summary,
        "rationale": finding.rationale,
        "impact": finding.impact,
        "files": finding.files,
        "tests": finding.tests,
        "acceptance": finding.acceptance,
        "version_bump": finding.version_bump,
        "target_version": finding.target_version,
        "baseline_action": finding.baseline_action,
        "work_item": work_item.model_dump(),
        "close_gate": {
            "requires_review": True,
            "requires_qa": True,
            "reopen_on_failed_review_or_qa": True,
        },
    }
    doc["self_upgrade_execution"] = {
        "stage": str(existing_execution.get("stage") or "queued"),
        "attempt_count": int(existing_execution.get("attempt_count") or 0),
        "last_run_at": str(existing_execution.get("last_run_at") or ""),
        "last_error": str(existing_execution.get("last_error") or ""),
        "last_feedback": list(existing_execution.get("last_feedback") or []),
        "branch_name": str(existing_execution.get("branch_name") or ""),
        "base_branch": str(existing_execution.get("base_branch") or ""),
        "source_repo_root": str(existing_execution.get("source_repo_root") or str(repo_root)),
        "worktree_path": normalized_worktree_hint,
        "pull_request_url": str(existing_execution.get("pull_request_url") or ""),
        "commit_sha": str(existing_execution.get("commit_sha") or ""),
        "closed_at": str(existing_execution.get("closed_at") or ""),
    }
    doc["self_upgrade_audit"] = audit_doc
    doc["self_upgrade_milestone"] = milestone_doc
    doc["documentation_policy"] = documentation_policy
    doc["execution_policy"] = {
        "issue_only_scope": True,
        "allowed_paths": list(work_item.allowed_paths or []),
        "worktree_hint": normalized_worktree_hint,
        "commit_message_template": f"{task_id}: {work_item.title}",
        "issue_id_required": True,
        "no_extra_optimization": True,
        "module": normalized_module,
        "review_role": work_item.review_role,
        "qa_role": work_item.qa_role,
        "milestone_manager_role": str(milestone_doc.get("manager_role") or ROLE_MILESTONE_MANAGER),
        "documentation_role": str(documentation_policy.get("documentation_role") or ROLE_DOCUMENTATION_AGENT),
    }
    _write_yaml(ledger_path, doc)
    improvement_store.upsert_delivery_task(doc)
    try:
        sync_out = sync_task_issue_from_doc(doc)
        if sync_out.get("ok") and str(sync_out.get("url") or "").strip():
            links["issue"] = str(sync_out.get("url") or "").strip()
            doc["links"] = links
        _write_yaml(ledger_path, doc)
        improvement_store.upsert_delivery_task(doc)
    except Exception:
        pass
    if (not _compat_file_mirror_enabled()) and ledger_path.exists():
        try:
            ledger_path.unlink()
        except Exception:
            pass
    return {"task_id": task_id, "ledger_path": str(ledger_path)}


def _issue_body(
    *,
    repo_root: Path,
    repo_locator: str,
    finding: UpgradeFinding,
    work_item: UpgradeWorkItem,
    fingerprint: str,
    marker: str = "",
    doc: Optional[dict[str, Any]] = None,
) -> str:
    module = _normalize_module_name(
        str(work_item.module or finding.module or "").strip(),
        paths=list(work_item.allowed_paths or finding.files or []),
        workstream_id=str(work_item.workstream_id or finding.workstream_id or ""),
        title=str(work_item.title or finding.title or ""),
        summary=str(work_item.summary or finding.summary or ""),
        lane=str(finding.lane or ""),
    )
    issue_marker = str(marker or "").strip() or f"<!-- teamos:self_upgrade:{fingerprint} -->"
    lines = [
        issue_marker,
        "# 自升级任务",
        "",
        f"- 类型: {_issue_type_token(finding.lane)}",
        f"- Module: {module}",
        f"- 仓库定位: {repo_locator}",
        f"- 仓库路径: {repo_root}",
        f"- 影响等级: {finding.impact}",
        f"- 版本变更: {finding.version_bump}",
        "",
        "## 版本与里程碑",
        "",
    ]
    lines.extend(_task_issue_milestone_lines(doc or {}, finding=finding))
    lines.extend(
        [
        "## 任务概要",
        "",
        _normalize_issue_text(work_item.summary or finding.summary),
        "",
        "## 背景与原因",
        "",
        _normalize_issue_text(finding.rationale, empty_fallback="(无)"),
        "",
        "## 范围内",
        "",
    ])
    lines.extend([f"- {x}" for x in (work_item.allowed_paths or finding.files or [])] or ["- （未指定）"])
    lines.extend(
        [
            "",
            "## 范围外",
            "",
            "- 除上述路径外均不在本工单范围内。",
            "- 不允许顺手优化、额外重构或修改无关模块。",
            "",
            "## 测试与验证",
            "",
        ]
    )
    lines.extend([f"- {x}" for x in (work_item.tests or finding.tests or [])] or ["- （未指定）"])
    lines.extend(["", "## 验收标准", ""])
    lines.extend([f"- {x}" for x in (work_item.acceptance or finding.acceptance or [])] or ["- （未指定）"])
    lines.extend(
        [
            "",
            "## 风险与回滚",
            "",
            "- 如验证失败或 QA 未通过，必须回退本任务改动并保持 issue 处于 blocked/reopened 状态。",
            "- 任何超出 allowed_paths 的改动都应视为越界并驳回。",
            "",
            "## 审计状态",
            "",
        ]
    )
    lines.extend(_task_issue_audit_lines(doc or {}, finding=finding))
    lines.extend(
        [
            "",
            "## 文档同步",
            "",
        ]
    )
    lines.extend(_task_issue_documentation_lines(doc or {}, finding=finding, work_item=work_item))
    lines.extend(
        [
            "",
            "## 执行约束",
            "",
            f"- 编码角色: {role_display_zh(work_item.owner_role)} ({work_item.owner_role})",
            f"- 评审角色: {role_display_zh(work_item.review_role)} ({work_item.review_role})",
            f"- QA 角色: {role_display_zh(work_item.qa_role)} ({work_item.qa_role})",
            f"- worktree_hint: {work_item.worktree_hint or '(无)'}",
            "- issue_only_scope: true",
            "- no_extra_optimization: true",
            "- 提交信息必须包含任务号，格式示例: TASK-ID: 标题",
            "",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def _ensure_issue_record(*, repo_locator: str, repo_root: Path, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> _IssueRecord:
    if not repo_locator:
        return _IssueRecord(title=_issue_title_for_work_item(repo_root.name, finding, work_item), error="missing_repo_locator")
    finding = _localize_finding_to_zh(finding)
    work_item = work_item.model_copy(
        update={
            "module": _normalize_module_name(
                str(work_item.module or finding.module or "").strip(),
                paths=list(work_item.allowed_paths or finding.files or []),
                workstream_id=str(work_item.workstream_id or finding.workstream_id or ""),
                title=str(work_item.title or finding.title or ""),
                summary=str(work_item.summary or finding.summary or ""),
                lane=str(finding.lane or ""),
            )
        }
    )
    finding = finding.model_copy(update={"module": _normalize_module_name(str(finding.module or work_item.module), paths=list(finding.files or work_item.allowed_paths or []), workstream_id=str(finding.workstream_id or ""), title=str(finding.title or ""), summary=str(finding.summary or ""), lane=str(finding.lane or ""))})
    title = _issue_title_for_work_item(repo_root.name, finding, work_item)
    fingerprint = _finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding) + "-" + _slug(work_item.title, default="work")
    marker = _task_issue_marker(repo_locator=repo_locator, repo_root=repo_root, finding=finding, work_item=work_item)
    body = _issue_body(repo_root=repo_root, repo_locator=repo_locator, finding=finding, work_item=work_item, fingerprint=fingerprint, marker=marker, doc={})
    labels = _task_issue_labels(doc={}, finding=finding, work_item=work_item)
    milestone = _task_issue_milestone_number(repo_locator=repo_locator, finding=finding)
    try:
        issue = ensure_issue(
            repo_locator,
            title=title,
            body=body,
            allow_create=True,
            labels=labels,
            milestone=milestone,
            marker=marker,
        )
        issue = update_issue(repo_locator, issue.number, title=title, body=body, labels=labels, state="open", milestone=milestone)
        return _IssueRecord(title=title, url=str(issue.url or ""))
    except (GitHubAuthError, GitHubIssuesBusError) as e:
        return _IssueRecord(title=title, error=str(e)[:500])


def _issue_number_from_url(issue_url: str) -> int:
    m = re.search(r"/issues/(?P<number>\d+)(?:$|[?#])", str(issue_url or "").strip())
    return int(m.group("number")) if m else 0


def sync_task_issue_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    finding, work_item = _finding_from_task_doc(doc)
    if finding is None or work_item is None:
        return {"ok": False, "reason": "missing_self_upgrade_finding"}
    milestone_out = sync_milestone_from_doc(doc)
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    repo_locator = str(repo.get("locator") or "").strip()
    repo_root = Path(str(repo.get("source_workdir") or repo.get("workdir") or team_os_root())).resolve()
    if not repo_locator:
        return {"ok": False, "reason": "missing_repo_locator"}
    links = doc.get("links") or {}
    if not isinstance(links, dict):
        links = {}
    issue_number = _issue_number_from_url(str(links.get("issue") or ""))
    title = _issue_title_for_work_item(repo_root.name, finding, work_item)
    fingerprint = str((((doc.get("orchestration") or {}) if isinstance(doc.get("orchestration"), dict) else {}).get("finding_fingerprint")) or _finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding))
    marker = _task_issue_marker(repo_locator=repo_locator, repo_root=repo_root, finding=finding, work_item=work_item)
    body = _issue_body(repo_root=repo_root, repo_locator=repo_locator, finding=finding, work_item=work_item, fingerprint=fingerprint, marker=marker, doc=doc)
    labels = _task_issue_labels(doc=doc, finding=finding, work_item=work_item)
    milestone = _task_issue_milestone_number(repo_locator=repo_locator, finding=finding, doc=doc)
    issue_state = "closed" if str(doc.get("status") or "").strip().lower() in ("closed", "done") else "open"
    try:
        if issue_number > 0:
            issue = update_issue(repo_locator, issue_number, title=title, body=body, labels=labels, state=issue_state, milestone=milestone)
        else:
            created = _ensure_issue_record(repo_locator=repo_locator, repo_root=repo_root, finding=finding, work_item=work_item)
            if created.error or not created.url:
                return {"ok": False, "reason": created.error or "issue_create_failed", "title": created.title}
            issue_number = _issue_number_from_url(created.url)
            issue = update_issue(repo_locator, issue_number, title=title, body=body, labels=labels, state=issue_state, milestone=milestone)
            links["issue"] = str(issue.url or created.url)
            doc["links"] = links
        return {
            "ok": True,
            "number": int(issue.number),
            "url": str(issue.url or ""),
            "title": str(issue.title or title),
            "labels": labels,
            "milestone": milestone or 0,
            "milestone_sync": milestone_out,
        }
    except (GitHubAuthError, GitHubIssuesBusError) as e:
        return {"ok": False, "reason": str(e)[:500], "title": title}


def _finding_from_task_doc(doc: dict[str, Any]) -> tuple[Optional[UpgradeFinding], Optional[UpgradeWorkItem]]:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        return None, None
    work_item_raw = su.get("work_item") or {}
    if not isinstance(work_item_raw, dict):
        work_item_raw = {}
    try:
        work_item = UpgradeWorkItem(
            title=str(work_item_raw.get("title") or doc.get("title") or "").strip() or str(doc.get("title") or "未命名任务"),
            summary=str(work_item_raw.get("summary") or su.get("summary") or "").strip(),
            owner_role=_normalize_owner_role(str(work_item_raw.get("owner_role") or doc.get("owner_role") or "").strip(), str(su.get("lane") or "bug")),
            review_role=_normalize_review_role(str(work_item_raw.get("review_role") or ((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("review_role") or "").strip()),
            qa_role=_normalize_qa_role(str(work_item_raw.get("qa_role") or ((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("qa_role") or "").strip()),
            workstream_id=str(work_item_raw.get("workstream_id") or doc.get("workstream_id") or su.get("workstream_id") or "general").strip() or "general",
            allowed_paths=[str(x).strip() for x in (work_item_raw.get("allowed_paths") or ((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("allowed_paths") or su.get("files") or []) if str(x).strip()],
            tests=[str(x).strip() for x in (work_item_raw.get("tests") or su.get("tests") or []) if str(x).strip()],
            acceptance=[str(x).strip() for x in (work_item_raw.get("acceptance") or su.get("acceptance") or []) if str(x).strip()],
            worktree_hint=str(work_item_raw.get("worktree_hint") or ((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("worktree_hint") or "").strip(),
            module=_normalize_module_name(
                str(work_item_raw.get("module") or su.get("module") or ((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("module") or "").strip(),
                paths=[str(x).strip() for x in (work_item_raw.get("allowed_paths") or ((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("allowed_paths") or su.get("files") or []) if str(x).strip()],
                workstream_id=str(work_item_raw.get("workstream_id") or doc.get("workstream_id") or su.get("workstream_id") or "general").strip(),
                title=str(work_item_raw.get("title") or doc.get("title") or "").strip(),
                summary=str(work_item_raw.get("summary") or su.get("summary") or "").strip(),
                lane=str(su.get("lane") or "bug"),
            ),
        )
        finding = UpgradeFinding(
            kind=str(su.get("kind") or "BUG").strip() or "BUG",
            lane=str(su.get("lane") or "bug").strip() or "bug",
            title=str(work_item.title or doc.get("title") or "未命名任务").strip(),
            summary=str(su.get("summary") or work_item.summary or "").strip(),
            module=_normalize_module_name(
                str(su.get("module") or work_item.module or "").strip(),
                paths=[str(x).strip() for x in (su.get("files") or work_item.allowed_paths or []) if str(x).strip()],
                workstream_id=str(doc.get("workstream_id") or work_item.workstream_id or "general").strip(),
                title=str(work_item.title or doc.get("title") or ""),
                summary=str(su.get("summary") or work_item.summary or ""),
                lane=str(su.get("lane") or "bug"),
            ),
            rationale=str(su.get("rationale") or "").strip(),
            impact=str(su.get("impact") or "MED").strip() or "MED",
            workstream_id=str(doc.get("workstream_id") or work_item.workstream_id or "general").strip() or "general",
            files=[str(x).strip() for x in (su.get("files") or work_item.allowed_paths or []) if str(x).strip()],
            tests=[str(x).strip() for x in (su.get("tests") or work_item.tests or []) if str(x).strip()],
            acceptance=[str(x).strip() for x in (su.get("acceptance") or work_item.acceptance or []) if str(x).strip()],
            version_bump=str(su.get("version_bump") or "patch").strip() or "patch",
            target_version=str(su.get("target_version") or "").strip(),
            baseline_action=str(su.get("baseline_action") or "").strip(),
            requires_user_confirmation=bool(su.get("requires_user_confirmation") or False),
            cooldown_hours=int(su.get("cooldown_hours") or 0),
            work_items=[work_item],
        )
        return finding, work_item
    except Exception:
        return None, None


def sync_existing_self_upgrade_github_content_to_zh(*, project_id: str = "teamos") -> dict[str, Any]:
    stats = {"proposals": 0, "proposal_issues": 0, "tasks": 0, "task_issues": 0, "errors": 0}
    for proposal in list_proposals():
        try:
            pid = str(proposal.get("proposal_id") or "").strip()
            if not pid:
                continue
            localized = _localize_proposal_doc_to_zh(proposal)
            localized = _update_proposal_record(
                pid,
                title=str(localized.get("title") or "").strip(),
                summary=str(localized.get("summary") or "").strip(),
                extra={
                    "module": _proposal_module(localized),
                    "rationale": str(localized.get("rationale") or "").strip(),
                    "work_items": list(localized.get("work_items") or []),
                },
            )
            stats["proposals"] += 1
            if str(localized.get("repo_locator") or "").strip():
                _ensure_proposal_discussion_issue(localized)
                stats["proposal_issues"] += 1
        except Exception:
            stats["errors"] += 1
    for localized_doc in _iter_self_upgrade_task_docs(project_id=project_id):
        try:
            if not isinstance(localized_doc, dict) or not _is_self_upgrade_task_doc(localized_doc):
                continue
            original_doc = dict(localized_doc)
            localized_doc = _localize_task_doc_to_zh(localized_doc)
            finding, work_item = _finding_from_task_doc(localized_doc)
            if finding is not None and work_item is not None:
                localized_doc["title"] = _task_title_for_work_item(finding, work_item)
                repo_info = localized_doc.get("repo") or {}
                if not isinstance(repo_info, dict):
                    repo_info = {}
                repo_locator_for_fp = str(repo_info.get("locator") or "").strip()
                repo_root_for_fp = Path(str(repo_info.get("source_workdir") or repo_info.get("workdir") or team_os_root())).resolve()
                orchestration = localized_doc.get("orchestration") or {}
                if isinstance(orchestration, dict):
                    orchestration = dict(orchestration)
                    orchestration["finding_fingerprint"] = str(
                        orchestration.get("finding_fingerprint")
                        or _finding_fingerprint(repo_locator=repo_locator_for_fp, repo_root=repo_root_for_fp, finding=finding)
                    )
                    orchestration["work_item_key"] = str(orchestration.get("work_item_key") or _work_item_key(work_item))
                    localized_doc["orchestration"] = orchestration
                su = localized_doc.get("self_upgrade") or {}
                if isinstance(su, dict):
                    su = dict(su)
                    su["module"] = finding.module
                    wi = su.get("work_item") or {}
                    if isinstance(wi, dict):
                        wi = dict(wi)
                        wi["module"] = work_item.module
                        su["work_item"] = wi
                    localized_doc["self_upgrade"] = su
                execution_policy = localized_doc.get("execution_policy") or {}
                if isinstance(execution_policy, dict):
                    execution_policy = dict(execution_policy)
                    execution_policy["module"] = work_item.module
                    localized_doc["execution_policy"] = execution_policy
            artifacts = localized_doc.get("artifacts") or {}
            if not isinstance(artifacts, dict):
                artifacts = {}
            ledger_path_raw = str(artifacts.get("ledger_path") or "").strip()
            ledger_path = Path(ledger_path_raw).expanduser().resolve() if ledger_path_raw else _task_ledger_dir(project_id) / f"{str(localized_doc.get('id') or localized_doc.get('task_id') or 'task').strip()}.yaml"
            if localized_doc != original_doc:
                _write_yaml(ledger_path, localized_doc)
            stats["tasks"] += 1
            sync_out = sync_task_issue_from_doc(localized_doc)
            if sync_out.get("ok"):
                _write_yaml(ledger_path, localized_doc)
                stats["task_issues"] += 1
            elif str(sync_out.get("reason") or "").strip():
                stats["errors"] += 1
        except Exception:
            stats["errors"] += 1
    return stats


def _sync_panel(*, db, project_id: str) -> dict[str, Any]:
    svc = GitHubProjectsPanelSync(db=db)
    try:
        return svc.sync(project_id=project_id, mode="full", dry_run=False)
    except (GitHubAPIError, GitHubAuthError, PanelMappingError, PanelSyncError) as e:
        return {"ok": False, "error": str(e)[:500], "project_id": project_id}


def _register_agents(*, db, project_id: str, workstream_id: str, task_id: str) -> dict[str, str]:
    return crewai_role_registry.register_team_blueprint(
        db=db,
        blueprint=crewai_role_registry.planning_team_blueprint(),
        project_id=project_id,
        workstream_id=workstream_id,
        task_id=task_id,
    )


def _finish_agents(*, db, agent_ids: dict[str, str], state: str, current_action: str) -> None:
    for agent_id in agent_ids.values():
        try:
            db.update_assignment(agent_id=agent_id, state=state, current_action=current_action)
        except Exception:
            pass


def _record_from_materialized_item(
    *,
    target_id: str,
    repo_root: Path,
    repo_locator: str,
    project_id: str,
    finding: UpgradeFinding,
    work_item: UpgradeWorkItem,
    proposal_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    workflow = crewai_workflow_registry.workflow_for_lane(finding.lane)
    if dry_run:
        return {
            "workflow_id": workflow.workflow_id,
            "lane": finding.lane,
            "kind": finding.kind,
            "title": work_item.title,
            "task_id": "",
            "task_ledger": "",
            "issue_title": _issue_title_for_work_item(repo_root.name, finding, work_item),
            "issue_url": "",
            "issue_error": "",
            "workstream_id": work_item.workstream_id or finding.workstream_id,
            "tests": work_item.tests or finding.tests,
            "acceptance": work_item.acceptance or finding.acceptance,
            "owner_role": work_item.owner_role,
            "review_role": work_item.review_role,
            "qa_role": work_item.qa_role,
            "version_bump": finding.version_bump,
            "target_version": finding.target_version,
            "proposal_id": proposal_id,
            "dry_run": True,
        }
    issue = _ensure_issue_record(repo_locator=repo_locator, repo_root=repo_root, finding=finding, work_item=work_item)
    task = _ensure_task_record(
        target_id=target_id,
        repo_root=repo_root,
        repo_locator=repo_locator,
        panel_project_id=project_id,
        finding=finding,
        work_item=work_item,
        issue=issue,
        proposal_id=proposal_id,
    )
    return {
        "workflow_id": workflow.workflow_id,
        "lane": finding.lane,
        "kind": finding.kind,
        "title": work_item.title,
        "task_id": str(task.get("task_id") or ""),
        "task_ledger": str(task.get("ledger_path") or ""),
        "issue_title": issue.title,
        "issue_url": issue.url,
        "issue_error": issue.error,
        "workstream_id": work_item.workstream_id or finding.workstream_id,
        "tests": work_item.tests or finding.tests,
        "acceptance": work_item.acceptance or finding.acceptance,
        "owner_role": work_item.owner_role,
        "review_role": work_item.review_role,
        "qa_role": work_item.qa_role,
        "version_bump": finding.version_bump,
        "target_version": finding.target_version,
        "proposal_id": proposal_id,
        "worktree_hint": work_item.worktree_hint,
    }


def _mark_proposal_materialized(proposal_id: str) -> None:
    doc = improvement_store.get_proposal(proposal_id)
    if not isinstance(doc, dict):
        return
    doc["status"] = "MATERIALIZED"
    doc["materialized_at"] = _utc_now_iso()
    doc["updated_at"] = _utc_now_iso()
    improvement_store.upsert_proposal(doc)


def run_self_upgrade(*, db, spec: Any, actor: str, run_id: str, crewai_info: dict[str, Any]) -> dict[str, Any]:
    project_id = _panel_project_id(str(getattr(spec, "project_id", "teamos") or "teamos"))
    target = _resolve_target(
        target_id=str(getattr(spec, "target_id", "") or ""),
        repo_path=str(getattr(spec, "repo_path", "") or ""),
        repo_url=str(getattr(spec, "repo_url", "") or ""),
        repo_locator=str(getattr(spec, "repo_locator", "") or ""),
        project_id=project_id,
    )
    target_id = str(target.get("target_id") or "").strip() or "teamos"
    repo_root = Path(str(target.get("repo_root") or team_os_root())).expanduser().resolve()
    repo_context = collect_repo_context(repo_root=repo_root, explicit_repo_locator=str(target.get("repo_locator") or ""), target_id=target_id)
    repo_locator = str(repo_context.get("repo_locator") or target.get("repo_locator") or "").strip()
    workstream_id = str(getattr(spec, "workstream_id", "") or "general").strip() or "general"
    force = bool(getattr(spec, "force", False))
    dry_run = bool(getattr(spec, "dry_run", False))
    trigger = str(getattr(spec, "trigger", "") or "manual").strip() or "manual"
    max_findings = max(1, min(int(os.getenv("TEAMOS_SELF_UPGRADE_MAX_FINDINGS", "3") or "3"), 10))
    run_started_at = _utc_now_iso()

    should_skip, skip_reason = _should_skip(target_id=target_id, repo_root=repo_root, force=force)
    if should_skip:
        payload = {
            "ok": True,
            "skipped": True,
            "reason": skip_reason,
            "run_id": run_id,
            "target_id": target_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "trigger": trigger,
            "crewai": crewai_info,
        }
        _merge_state_last_run(
            target_id,
            {"ts": _utc_now_iso(), "target_id": target_id, "repo_root": str(repo_root), "repo_locator": repo_locator, "status": "SKIPPED", "reason": skip_reason},
        )
        db.add_event(
            event_type="SELF_UPGRADE_SKIPPED",
            actor=actor,
            project_id=project_id,
            workstream_id=workstream_id,
            payload=payload,
        )
        return payload

    agent_ids = _register_agents(db=db, project_id=project_id, workstream_id=workstream_id, task_id=str(getattr(spec, "task_id", "") or ""))
    db.add_event(
        event_type="SELF_UPGRADE_STARTED",
        actor=actor,
        project_id=project_id,
        workstream_id=workstream_id,
        payload={
            "run_id": run_id,
            "target_id": target_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "trigger": trigger,
            "dry_run": dry_run,
        },
    )

    try:
        plan, crew_debug = kickoff_upgrade_plan(repo_context=repo_context, max_findings=max_findings, verbose=_env_truthy("TEAMOS_SELF_UPGRADE_VERBOSE", "0"))
    except Exception as e:
        _finish_agents(db=db, agent_ids=agent_ids, state="FAILED", current_action="self-upgrade failed")
        backoff_until = ""
        err_text = str(e)
        if any(x in err_text.lower() for x in ("insufficient_quota", "429", "rate limit")):
            import datetime as _dt

            backoff_hours = max(1, int(os.getenv("TEAMOS_SELF_UPGRADE_FAILURE_BACKOFF_HOURS", "1") or "1"))
            backoff_until = (
                _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=backoff_hours)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        _merge_state_last_run(
            target_id,
            {
                "ts": _utc_now_iso(),
                "target_id": target_id,
                "repo_root": str(repo_root),
                "repo_locator": repo_locator,
                "status": "FAILED",
                "error": err_text[:500],
            },
            backoff_until=backoff_until,
        )
        _append_run_history(
            target_id,
            {
                "ts": _utc_now_iso(),
                "run_id": run_id,
                "target_id": target_id,
                "status": "FAILED",
                "repo_root": str(repo_root),
                "repo_locator": repo_locator,
                "error": err_text[:300],
            }
        )
        raise SelfUpgradeError(str(e)) from e

    records: list[dict[str, Any]] = []
    pending_proposals: list[dict[str, Any]] = []
    current_version = str(plan.current_version or repo_context.get("current_version") or "0.1.0").strip() or "0.1.0"
    for finding in plan.findings:
        workflow = crewai_workflow_registry.workflow_for_lane(finding.lane)
        requires_confirmation = workflow.requires_user_confirmation or bool(finding.requires_user_confirmation)
        work_items = list(finding.work_items or []) or _default_work_items(repo_root=repo_root, finding=finding)
        if not workflow.uses_proposal:
            for work_item in work_items:
                record = _record_from_materialized_item(
                    target_id=target_id,
                    repo_root=repo_root,
                    repo_locator=repo_locator,
                    project_id=project_id,
                    finding=finding,
                    work_item=work_item,
                    proposal_id="",
                    dry_run=dry_run,
                )
                records.append(record)
                db.add_event(
                    event_type="SELF_UPGRADE_RECORD_CREATED",
                    actor=actor,
                    project_id=project_id,
                    workstream_id=work_item.workstream_id or finding.workstream_id or workstream_id,
                    payload={"run_id": run_id, **record},
                )
            continue

        proposal = _upsert_proposal(
            target_id=target_id,
            repo_root=repo_root,
            repo_locator=repo_locator,
            project_id=project_id,
            finding=finding,
            current_version=current_version,
        )
        if requires_confirmation:
            proposal = _ensure_proposal_discussion_issue(proposal)
        proposal_id = str(proposal.get("proposal_id") or "")
        status = str(proposal.get("status") or "").strip().upper()
        due = _proposal_due(proposal)
        should_materialize = workflow.should_materialize(status=status, due=due)

        if should_materialize:
            for work_item in work_items:
                record = _record_from_materialized_item(
                    target_id=target_id,
                    repo_root=repo_root,
                    repo_locator=repo_locator,
                    project_id=project_id,
                    finding=finding,
                    work_item=work_item,
                    proposal_id=proposal_id,
                    dry_run=dry_run,
                )
                records.append(record)
                db.add_event(
                    event_type="SELF_UPGRADE_RECORD_CREATED",
                    actor=actor,
                    project_id=project_id,
                    workstream_id=work_item.workstream_id or finding.workstream_id or workstream_id,
                    payload={"run_id": run_id, **record},
                )
            if not dry_run:
                _mark_proposal_materialized(proposal_id)
            db.add_event(
                event_type="SELF_UPGRADE_PROPOSAL_MATERIALIZED",
                actor=actor,
                project_id=project_id,
                workstream_id=finding.workstream_id or workstream_id,
                payload={"run_id": run_id, "proposal_id": proposal_id, "lane": finding.lane, "title": finding.title, "records": len(work_items)},
            )
            continue

        pending_doc = {
            "proposal_id": proposal_id,
            "workflow_id": str(proposal.get("workflow_id") or workflow.workflow_id),
            "lane": finding.lane,
            "title": proposal.get("title") or finding.title,
            "status": status,
            "cooldown_until": proposal.get("cooldown_until") or "",
            "version_bump": proposal.get("version_bump") or finding.version_bump,
            "target_version": proposal.get("target_version") or finding.target_version,
            "requires_user_confirmation": bool(proposal.get("requires_user_confirmation")),
            "discussion_issue_url": proposal.get("discussion_issue_url") or "",
            "discussion_issue_number": int(proposal.get("discussion_issue_number") or 0),
        }
        pending_proposals.append(pending_doc)
        db.add_event(
            event_type="SELF_UPGRADE_PROPOSAL_PENDING",
            actor=actor,
            project_id=project_id,
            workstream_id=finding.workstream_id or workstream_id,
            payload={"run_id": run_id, **pending_doc},
        )

    if dry_run:
        try:
            svc = GitHubProjectsPanelSync(db=db)
            panel_sync = svc.sync(project_id=project_id, mode="full", dry_run=True)
        except Exception as e:
            panel_sync = {"ok": False, "dry_run": True, "error": str(e)[:500], "project_id": project_id}
    else:
        panel_sync = _sync_panel(db=db, project_id=project_id)
    report = {
        "ts": _utc_now_iso(),
        "run_id": run_id,
        "target_id": target_id,
        "actor": actor,
        "trigger": trigger,
        "target": target,
        "repo_context": repo_context,
        "plan": plan.model_dump(),
        "records": records,
        "pending_proposals": pending_proposals,
        "panel_sync": panel_sync,
        "crewai": crewai_info,
        "crew_debug": crew_debug,
    }
    improvement_store.save_report(target_id=target_id, project_id=project_id, report=report)
    _merge_state_last_run(
        target_id,
        {
            "ts": _utc_now_iso(),
            "run_id": run_id,
            "target_id": target_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "status": "DONE",
            "records": len(records),
            "pending_proposals": len(pending_proposals),
            "report_id": run_id,
        },
    )
    _append_run_history(
        target_id,
        {
            "ts": _utc_now_iso(),
            "run_id": run_id,
            "target_id": target_id,
            "status": "DONE",
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "records": len(records),
            "pending_proposals": len(pending_proposals),
        }
    )
    _finish_agents(db=db, agent_ids=agent_ids, state="DONE", current_action="self-upgrade recorded")
    db.add_event(
        event_type="SELF_UPGRADE_FINISHED",
        actor=actor,
        project_id=project_id,
        workstream_id=workstream_id,
        payload={
            "run_id": run_id,
            "target_id": target_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "records": len(records),
            "pending_proposals": len(pending_proposals),
            "panel_sync": panel_sync,
            "report_id": run_id,
        },
    )
    return {
        "ok": True,
        "run_id": run_id,
        "target_id": target_id,
        "repo_root": str(repo_root),
        "repo_locator": repo_locator,
        "project_id": project_id,
        "summary": plan.summary,
        "ci_actions": plan.ci_actions,
        "notes": plan.notes,
        "current_version": current_version,
        "planned_version": plan.planned_version or _planned_version(current_version, plan.findings),
        "records": records,
        "pending_proposals": pending_proposals,
        "panel_sync": panel_sync,
        "report_id": run_id,
        "crewai": crewai_info,
        "dry_run": dry_run,
        "write_delegate": {
            "write_mode": "crewai_self_upgrade",
            "writer": "crewai_agents",
            "truth_sources": ["task_ledger", "github_issues", "github_projects"],
            "target_repo": repo_locator or str(repo_root),
        },
    }
