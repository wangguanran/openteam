from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import configparser
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from app import codex_llm
from app import crew_tools
from app import crewai_agent_factory
from app import crewai_llm_factory
from app import crewai_role_registry
from app import crewai_runtime
from app import crewai_team_registry
from app import crewai_workflow_registry
from app import improvement_store
from app import workspace_store
from app.github_issues_bus import GitHubIssuesBusError, ensure_issue, ensure_milestone, list_issue_comments, update_issue, upsert_comment_with_marker
from app.github_projects_client import GitHubAPIError, GitHubAuthError
from app.panel_github_sync import GitHubProjectsPanelSync, PanelSyncError
from app.panel_mapping import PanelMappingError, get_project_cfg, load_mapping
from app.plan_store import upsert_runtime_milestone
from app.pydantic_compat import BaseModel, Field
from app.state_store import ledger_tasks_dir, runtime_state_root, team_os_root
from app.workflow_models import ProposalDiscussionResponse
from app.workflow_models import StructuredBugCandidate
from app.workflow_models import StructuredBugScanResult
from app.workflow_models import UpgradeFinding
from app.workflow_models import UpgradePlan
from app.workflow_models import UpgradeWorkItem


class TeamWorkflowError(RuntimeError):
    pass


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
ROLE_BUG_REPRO_AGENT = crewai_role_registry.ROLE_BUG_REPRO_AGENT
ROLE_BUG_TESTCASE_AGENT = crewai_role_registry.ROLE_BUG_TESTCASE_AGENT
ROLE_TEST_CASE_GAP_AGENT = crewai_role_registry.ROLE_TEST_CASE_GAP_AGENT
ROLE_DOCUMENTATION_AGENT = crewai_role_registry.ROLE_DOCUMENTATION_AGENT
ROLE_MILESTONE_MANAGER = crewai_role_registry.ROLE_MILESTONE_MANAGER
ROLE_CODE_QUALITY_ANALYST = crewai_role_registry.ROLE_CODE_QUALITY_ANALYST
ROLE_CODING_AGENT = crewai_role_registry.ROLE_CODING_AGENT
ROLE_FEATURE_CODING_AGENT = crewai_role_registry.ROLE_FEATURE_CODING_AGENT
ROLE_BUGFIX_CODING_AGENT = crewai_role_registry.ROLE_BUGFIX_CODING_AGENT
ROLE_PROCESS_OPTIMIZATION_AGENT = crewai_role_registry.ROLE_PROCESS_OPTIMIZATION_AGENT
ROLE_CODE_QUALITY_AGENT = crewai_role_registry.ROLE_CODE_QUALITY_AGENT

MODULE_ALIASES = {
    "runtime": "Runtime",
    "team-workflow": "Team-Workflow",
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
    (("postgres", "redis", "hub"), "Hub"),
    (("requirements", "raw_inputs", "requirement"), "Requirements"),
    (("observability", "metrics", "telemetry", "heartbeat"), "Observability"),
    (("security", "auth", "oauth", "token"), "Security"),
    (("team_workflow",), "Team-Workflow"),
    (("control-plane", "orchestrator", "main.py", "runtime"), "Runtime"),
]


class LocalizedWorkItemText(BaseModel):
    title: str = ""
    summary: str = ""
    acceptance: list[str] = Field(default_factory=list)
    why_not_covered: str = ""


class LocalizedFindingText(BaseModel):
    title: str = ""
    summary: str = ""
    rationale: str = ""
    acceptance: list[str] = Field(default_factory=list)
    why_not_covered: str = ""
    work_items: list[LocalizedWorkItemText] = Field(default_factory=list)


class LocalizedProposalText(BaseModel):
    title: str = ""
    summary: str = ""
    rationale: str = ""
    why_not_covered: str = ""
    work_items: list[LocalizedWorkItemText] = Field(default_factory=list)


class LocalizedTaskText(BaseModel):
    task_title: str = ""
    title: str = ""
    summary: str = ""
    rationale: str = ""
    acceptance: list[str] = Field(default_factory=list)
    why_not_covered: str = ""


def _env_truthy(name: str, default: str = "0") -> bool:
    raw = os.getenv(name)
    return str(raw if raw is not None else default).strip().lower() not in ("", "0", "false", "no", "off")


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ts_compact_utc() -> str:
    return _utc_now_iso().replace(":", "").replace("-", "")


def _slug(text: str, *, default: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return s or default


def _default_team_id() -> str:
    return str(crewai_team_registry.default_team_id() or "").strip()


def _normalize_team_id(team_id: Any = "") -> str:
    return str(team_id or "").strip() or _default_team_id()


def _team_flow_id(team_id: Any = "") -> str:
    return f"team:{_normalize_team_id(team_id)}"


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")
role_display_zh = crewai_role_registry.role_display_zh


def _module_slug(module: str) -> str:
    return _slug(module, default="team-workflow")


def _is_team_flow(flow: Any, *, team_id: Any = "") -> bool:
    normalized = str(flow or "").strip().lower()
    if not normalized.startswith("team:"):
        return False
    if str(team_id or "").strip():
        return normalized == _team_flow_id(team_id).lower()
    return True


def _team_section(doc: dict[str, Any], *, key: str) -> dict[str, Any]:
    value = doc.get(key)
    return dict(value) if isinstance(value, dict) else {}


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
    team_aliases = {
        _module_slug(str(spec.team_id or "")): "Team-Workflow"
        for spec in crewai_team_registry.list_teams()
        if str(spec.team_id or "").strip()
    }
    aliases = dict(MODULE_ALIASES)
    aliases.update(team_aliases)
    if raw_slug in aliases:
        return aliases[raw_slug]

    bag = " | ".join(
        [
            str(raw or ""),
            str(workstream_id or ""),
            str(title or ""),
            str(summary or ""),
            " ".join([str(x).strip() for x in (paths or []) if str(x).strip()]),
        ]
    ).lower()
    if any(team_slug and team_slug in bag for team_slug in team_aliases):
        return "Team-Workflow"
    for needles, module in MODULE_RULES:
        if any(needle in bag for needle in needles):
            return module

    if raw_slug and raw_slug not in ("item", "general"):
        return "-".join([part.capitalize() for part in raw_slug.split("-") if part]) or "Team-Workflow"
    if str(lane or "").strip().lower() == "bug":
        return "Runtime"
    return "Team-Workflow"


def _normalize_repo_doc_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/").lstrip("/")
    while raw.startswith("./"):
        raw = raw[2:]
    return raw


def _documentation_allowed_paths(*, module: str, lane: str, allowed_paths: list[str]) -> list[str]:
    out = ["README.md", "docs", "scaffolds/runtime/README.md"]
    module_norm = _normalize_module_name(module, paths=allowed_paths, lane=lane)
    lane_norm = str(lane or "").strip().lower()
    path_bag = [_normalize_repo_doc_path(x) for x in (allowed_paths or []) if _normalize_repo_doc_path(x)]
    if module_norm in ("CLI", "Doctor", "Bootstrap", "Runtime", "Team-Workflow", "CI", "Release", "GitHub-Project"):
        out.extend(
            [
                "docs/runbooks/EXECUTION_RUNBOOK.md",
                "docs/runbooks/REPO_BOOTSTRAP_AND_UPGRADE.md",
                "docs/product/GOVERNANCE.md",
            ]
        )
    if module_norm == "CI":
        out.append(".github/ISSUE_TEMPLATE")
    if lane_norm == "process":
        out.append("docs/plans")
    if any(path.startswith(".github/workflows") for path in path_bag):
        out.extend([".github/ISSUE_TEMPLATE", "docs/product/GOVERNANCE.md"])
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
    if module in ("CLI", "Doctor", "Bootstrap", "Runtime", "Team-Workflow", "CI", "Release", "GitHub-Project"):
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
    return crewai_workflow_registry.workflow_for_lane_phase(lane, crewai_workflow_registry.PHASE_FINDING).requires_user_confirmation


def _lane_max_candidates(lane: str, *, project_id: str = "teamos") -> int:
    return crewai_workflow_registry.workflow_for_lane_phase(lane, crewai_workflow_registry.PHASE_FINDING, project_id=project_id).max_candidates()


def _lane_max_continuous_runtime_minutes(lane: str, *, project_id: str = "teamos") -> int:
    return crewai_workflow_registry.workflow_for_lane_phase(lane, crewai_workflow_registry.PHASE_FINDING, project_id=project_id).max_continuous_runtime_minutes()


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
    return f"<!-- teamos:team_workflow:{fingerprint} -->"


def _normalize_owner_role(role_id: str, lane: str) -> str:
    rid = str(role_id or "").strip()
    _ = lane
    if rid in (
        "",
        "Coding-Agent",
        "Developer",
        "Developer-Agent",
        ROLE_FEATURE_CODING_AGENT,
        ROLE_BUGFIX_CODING_AGENT,
        ROLE_PROCESS_OPTIMIZATION_AGENT,
        ROLE_CODE_QUALITY_AGENT,
    ):
        return ROLE_CODING_AGENT
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
    if not _env_truthy("TEAMOS_RUNTIME_LOCALIZE_ZH", "1"):
        return False
    return shutil.which("codex") is not None


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


def _git_ls_files(root: Path, *, limit: int = 2000) -> list[str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception:
        return []
    if p.returncode != 0:
        return []
    out: list[str] = []
    for raw in str(p.stdout or "").splitlines():
        rel = str(raw or "").strip().replace("\\", "/")
        if not rel:
            continue
        out.append(rel)
        if len(out) >= limit:
            break
    return out


_INSPECTION_TEXT_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".sh",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
}
_INSPECTION_PRIORITY_NAMES = (
    "readme.md",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "makefile",
    "dockerfile",
    "run_workflow.py",
    "run_crewai.py",
    "src/__main__.py",
    "src/main.py",
    "main.py",
    "app.py",
)
_TEXT_PASS_MARKERS = (
    "TODO",
    "FIXME",
    "BUG",
    "HACK",
    "XXX",
    "NotImplementedError",
    "assert False",
    "except Exception: pass",
)


def _classify_repo_path(rel: str) -> str:
    path = str(rel or "").strip().replace("\\", "/").lower()
    if not path:
        return "other"
    if path.startswith(".github/workflows/"):
        return "workflow"
    if path.startswith("tests/") or "/tests/" in path or path.startswith("test_") or "/test_" in path:
        return "test"
    if path.startswith("docs/") or path.startswith("readme"):
        return "docs"
    if path.startswith("scripts/") or path.endswith(".sh"):
        return "script"
    if any(path.endswith(suffix) for suffix in (".toml", ".json", ".yaml", ".yml", ".ini", ".cfg")):
        return "config"
    if any(path.endswith(suffix) for suffix in _SOURCE_EXTENSIONS):
        return "source"
    return "other"


def _discover_test_command_candidates(root: Path, *, tracked_files: list[str]) -> list[str]:
    candidates: list[str] = []
    tracked = {str(x).strip() for x in (tracked_files or []) if str(x).strip()}

    if "Makefile" in tracked:
        makefile_text = _read_text(root / "Makefile", max_chars=12000)
        if re.search(r"(?m)^test\s*:", makefile_text):
            candidates.append("make test")
        if re.search(r"(?m)^check\s*:", makefile_text):
            candidates.append("make check")

    if "package.json" in tracked:
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except Exception:
            package = {}
        scripts = package.get("scripts") if isinstance(package, dict) else {}
        if isinstance(scripts, dict):
            if str(scripts.get("test") or "").strip():
                candidates.append("npm test")
            if str(scripts.get("lint") or "").strip():
                candidates.append("npm run lint")

    if "pyproject.toml" in tracked or "requirements.txt" in tracked or any(_classify_repo_path(path) == "test" for path in tracked):
        candidates.append("python -m unittest")
        candidates.append("pytest -q")

    if "go.mod" in tracked:
        candidates.append("go test ./...")
    if "Cargo.toml" in tracked:
        candidates.append("cargo test")

    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        cmd = str(item or "").strip()
        if not cmd or cmd in seen:
            continue
        out.append(cmd)
        seen.add(cmd)
    return out[:12]


def _select_baseline_commands(*, candidates: list[str]) -> list[str]:
    priority = {
        "make test": 0,
        "make check": 1,
        "python -m unittest": 2,
        "pytest -q": 3,
        "npm test": 4,
        "npm run lint": 5,
        "go test ./...": 6,
        "cargo test": 7,
    }
    selected: list[str] = []
    seen_families: set[str] = set()
    for cmd in sorted(
        (str(item or "").strip() for item in (candidates or [])),
        key=lambda item: (priority.get(item, 999), item),
    ):
        if not cmd:
            continue
        family = cmd.split(" ", 1)[0]
        if family in seen_families:
            continue
        selected.append(cmd)
        seen_families.add(family)
        if len(selected) >= 2:
            break
    return selected


def _tail_text(value: str, *, max_chars: int = 1600) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _run_repository_baseline_checks(root: Path, *, command_candidates: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cmd in _select_baseline_commands(candidates=command_candidates):
        started = time.time()
        try:
            proc = subprocess.run(
                ["sh", "-lc", cmd],
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=120,
            )
            status = "passed" if int(proc.returncode) == 0 else "failed"
            out.append(
                {
                    "command": cmd,
                    "status": status,
                    "returncode": int(proc.returncode),
                    "duration_sec": round(max(0.0, time.time() - started), 3),
                    "stdout_tail": _tail_text(proc.stdout),
                    "stderr_tail": _tail_text(proc.stderr),
                }
            )
        except subprocess.TimeoutExpired as exc:
            out.append(
                {
                    "command": cmd,
                    "status": "timeout",
                    "returncode": None,
                    "duration_sec": round(max(0.0, time.time() - started), 3),
                    "stdout_tail": _tail_text(exc.stdout or ""),
                    "stderr_tail": _tail_text(exc.stderr or ""),
                }
            )
        except Exception as exc:
            out.append(
                {
                    "command": cmd,
                    "status": "error",
                    "returncode": None,
                    "duration_sec": round(max(0.0, time.time() - started), 3),
                    "stdout_tail": "",
                    "stderr_tail": str(exc)[:500],
                }
            )
    return out


def _focus_file_candidates(tracked_files: list[str]) -> list[str]:
    tracked = [str(x).strip() for x in (tracked_files or []) if str(x).strip()]
    lower_map = {path.lower(): path for path in tracked}
    out: list[str] = []

    def _add(path: str) -> None:
        normalized = lower_map.get(str(path or "").lower())
        if normalized and normalized not in out:
            out.append(normalized)

    for name in _INSPECTION_PRIORITY_NAMES:
        _add(name)
    for path in tracked:
        rel = str(path).replace("\\", "/")
        rel_lower = rel.lower()
        if rel_lower.startswith(".github/workflows/"):
            _add(rel)
        if rel_lower.startswith("tests/") or rel_lower.startswith("src/"):
            _add(rel)
        if len(out) >= 24:
            break
    return out[:24]


def _focus_file_excerpts(root: Path, *, tracked_files: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rel in _focus_file_candidates(tracked_files):
        path = root / rel
        if path.suffix.lower() not in _INSPECTION_TEXT_EXTENSIONS and path.name.lower() not in ("makefile", "dockerfile"):
            continue
        excerpt = _read_text(path, max_chars=1600)
        if not excerpt:
            continue
        out.append(
            {
                "path": rel,
                "bytes": int(path.stat().st_size) if path.exists() else 0,
                "excerpt": excerpt,
            }
        )
        if len(out) >= 16:
            break
    return out


def _repository_text_pass(root: Path, *, tracked_files: list[str]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    suspicious_files: list[dict[str, Any]] = []
    marker_totals: dict[str, int] = {marker: 0 for marker in _TEXT_PASS_MARKERS}
    text_file_count = 0
    excerpted_file_count = 0
    total_chars_read = 0

    for rel in tracked_files:
        path = root / rel
        suffix = path.suffix.lower()
        if suffix not in _INSPECTION_TEXT_EXTENSIONS and path.name.lower() not in ("makefile", "dockerfile"):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        text_file_count += 1
        total_chars_read += len(content)
        lines = content.count("\n") + (0 if not content else 1)
        marker_hits: dict[str, int] = {}
        for marker in _TEXT_PASS_MARKERS:
            hits = int(content.count(marker))
            if hits:
                marker_totals[marker] = int(marker_totals.get(marker, 0)) + hits
                marker_hits[marker] = hits
        excerpt = content[:800]
        if excerpt:
            excerpted_file_count += 1
        entry = {
            "path": rel,
            "category": _classify_repo_path(rel),
            "bytes": int(path.stat().st_size) if path.exists() else 0,
            "line_count": lines,
            "excerpt": excerpt,
            "truncated": len(content) > len(excerpt),
        }
        if marker_hits:
            entry["marker_hits"] = marker_hits
            suspicious_files.append(
                {
                    "path": rel,
                    "marker_hits": marker_hits,
                }
            )
        entries.append(entry)

    return {
        "text_file_count": text_file_count,
        "excerpted_file_count": excerpted_file_count,
        "total_chars_read": total_chars_read,
        "marker_totals": {key: value for key, value in marker_totals.items() if int(value) > 0},
        "suspicious_files": suspicious_files[:40],
        "entries": entries[:120],
    }


def _module_file_text(content: str, *, max_chars: int = 4000) -> tuple[str, bool]:
    text = str(content or "")
    if len(text) <= max_chars:
        return text, False
    half = max(1, (max_chars - 32) // 2)
    return text[:half] + "\n...[TRUNCATED]...\n" + text[-half:], True


def _repository_chunk_label(rel: str) -> str:
    path = str(rel or "").strip().replace("\\", "/").lstrip("/")
    if not path:
        return "root"
    parts = [part for part in path.split("/") if part]
    if len(parts) == 1:
        stem = Path(parts[0]).stem
        return stem or "root"
    head = parts[0]
    if head in ("src", "tests", "projects"):
        if "." in parts[-1]:
            file_name = parts[-1]
            if file_name.startswith("__init__."):
                return "/".join(parts[:-1]) or head
            stem = Path(file_name).stem
            base = parts[:-1]
            return "/".join([*base, stem]) if stem else "/".join(base) or head
        if len(parts) >= 3 and "." not in parts[2]:
            return "/".join(parts[:3])
        if len(parts) >= 2 and "." not in parts[1]:
            return f"{head}/{parts[1]}"
    if "." in parts[-1]:
        file_name = parts[-1]
        if file_name.startswith("__init__."):
            return "/".join(parts[:-1]) or head
        stem = Path(file_name).stem
        base = parts[:-1]
        return "/".join([*base, stem]) if stem else "/".join(base) or head
    return head


def _repository_module_chunks(root: Path, *, tracked_files: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rel in tracked_files:
        path = root / rel
        suffix = path.suffix.lower()
        if suffix not in _INSPECTION_TEXT_EXTENSIONS and path.name.lower() not in ("makefile", "dockerfile"):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        module = _repository_chunk_label(rel)
        grouped.setdefault(module, []).append(
            {
                "path": rel,
                "category": _classify_repo_path(rel),
                "content": content,
            }
        )

    category_priority = {
        "source": 0,
        "test": 1,
        "config": 2,
        "workflow": 3,
        "script": 4,
        "docs": 5,
        "other": 6,
    }
    chunks: list[dict[str, Any]] = []
    for module, files in grouped.items():
        ordered = sorted(
            files,
            key=lambda item: (
                category_priority.get(str(item.get("category") or "other"), 99),
                str(item.get("path") or ""),
            ),
        )
        file_docs: list[dict[str, Any]] = []
        marker_totals: dict[str, int] = {}
        included_chars = 0
        omitted_count = 0
        for item in ordered:
            content = str(item.get("content") or "")
            rendered, truncated = _module_file_text(content)
            projected = included_chars + len(rendered)
            if file_docs and projected > 24000:
                omitted_count += 1
                continue
            marker_hits: dict[str, int] = {}
            for marker in _TEXT_PASS_MARKERS:
                hits = int(content.count(marker))
                if hits:
                    marker_totals[marker] = int(marker_totals.get(marker, 0)) + hits
                    marker_hits[marker] = hits
            file_doc = {
                "path": str(item.get("path") or ""),
                "category": str(item.get("category") or "other"),
                "content": rendered,
                "truncated": truncated,
                "original_chars": len(content),
            }
            if marker_hits:
                file_doc["marker_hits"] = marker_hits
            file_docs.append(file_doc)
            included_chars += len(rendered)
        if not file_docs:
            continue
        category_counts: dict[str, int] = {}
        for item in ordered:
            category = str(item.get("category") or "other")
            category_counts[category] = int(category_counts.get(category, 0)) + 1
        chunks.append(
            {
                "module": module,
                "file_count": len(ordered),
                "included_file_count": len(file_docs),
                "omitted_file_count": omitted_count,
                "included_chars": included_chars,
                "category_counts": category_counts,
                "marker_totals": marker_totals,
                "files": file_docs,
            }
        )

    return sorted(
        chunks,
        key=lambda item: (
            -int((item.get("category_counts") or {}).get("source") or 0),
            -int((item.get("category_counts") or {}).get("test") or 0),
            -int(item.get("file_count") or 0),
            str(item.get("module") or ""),
        ),
    )


def _repository_inspection(root: Path) -> dict[str, Any]:
    tracked_files = _git_ls_files(root)
    test_command_candidates = _discover_test_command_candidates(root, tracked_files=tracked_files)
    category_counts = {
        "source": 0,
        "test": 0,
        "docs": 0,
        "config": 0,
        "script": 0,
        "workflow": 0,
        "other": 0,
    }
    category_samples: dict[str, list[str]] = {key: [] for key in category_counts}
    for rel in tracked_files:
        category = _classify_repo_path(rel)
        category_counts[category] = int(category_counts.get(category, 0)) + 1
        bucket = category_samples.setdefault(category, [])
        if len(bucket) < 20:
            bucket.append(rel)

    top_dirs: dict[str, int] = {}
    for rel in tracked_files:
        top = rel.split("/", 1)[0] if "/" in rel else "."
        top_dirs[top] = int(top_dirs.get(top, 0)) + 1

    return {
        "tracked_file_count": len(tracked_files),
        "tracked_file_sample": tracked_files[:160],
        "top_level_directory_counts": [
            {"name": name, "count": count}
            for name, count in sorted(top_dirs.items(), key=lambda item: (-int(item[1]), str(item[0])))
        ][:20],
        "category_counts": category_counts,
        "category_samples": {key: value[:20] for key, value in category_samples.items()},
        "focus_file_excerpts": _focus_file_excerpts(root, tracked_files=tracked_files),
        "text_pass": _repository_text_pass(root, tracked_files=tracked_files),
        "module_chunks": _repository_module_chunks(root, tracked_files=tracked_files),
        "test_command_candidates": test_command_candidates,
        "baseline_checks": _run_repository_baseline_checks(root, command_candidates=test_command_candidates),
    }


def _bug_scan_module_chunks(repo_context: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    inspection = dict(repo_context.get("repository_inspection") or {})
    chunks = inspection.get("module_chunks") if isinstance(inspection.get("module_chunks"), list) else []
    candidates: list[dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        category_counts = dict(chunk.get("category_counts") or {})
        if int(category_counts.get("source") or 0) <= 0 and int(category_counts.get("test") or 0) <= 0:
            continue
        if int(chunk.get("included_chars") or 0) <= 80:
            continue
        candidates.append(chunk)
    def _priority(item: dict[str, Any]) -> tuple[int, int, int, int, str]:
        module = str(item.get("module") or "").strip()
        if module.startswith("src/") or module == "src":
            head_rank = 0
        elif module.startswith("tests/") or module == "tests":
            head_rank = 1
        elif module.startswith("crewai_agents/") or module == "crewai_agents":
            head_rank = 2
        elif module.startswith("projects/") or module == "projects":
            head_rank = 3
        else:
            head_rank = 4
        category_counts = dict(item.get("category_counts") or {})
        source_count = int(category_counts.get("source") or 0)
        test_count = int(category_counts.get("test") or 0)
        included_chars = int(item.get("included_chars") or 0)
        return (head_rank, -source_count, -test_count, -included_chars, module)

    out: list[dict[str, Any]] = []
    for chunk in sorted(candidates, key=_priority):
        out.append(chunk)
        if len(out) >= max(1, int(limit)):
            break
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
    home = str(os.getenv("TEAMOS_HOME") or "").strip()
    if home:
        return (Path(home).expanduser().resolve() / "runtime" / "default").resolve()
    return (Path.home() / ".teamos" / "runtime" / "default").resolve()


def _worktrees_root() -> Path:
    return (_runtime_root() / "workspace" / "worktrees").resolve()


def _discovery_worktrees_root() -> Path:
    return (_worktrees_root() / "discovery").resolve()


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


def _git_ref_exists(repo_root: Path, ref: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", f"{str(ref).strip()}^{{commit}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _remove_tree_force(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _preferred_discovery_ref(*, source_repo_root: Path, target: dict[str, Any]) -> str:
    default_branch = str(target.get("default_branch") or "").strip()
    branch, head_ref = _head_ref(source_repo_root)
    candidates: list[str] = []
    for branch_name in (default_branch, branch):
        name = str(branch_name or "").strip()
        if not name:
            continue
        candidates.extend([f"origin/{name}", name])
    if head_ref:
        candidates.append(head_ref)
    candidates.append("HEAD")
    seen: set[str] = set()
    for candidate in candidates:
        ref = str(candidate or "").strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        if ref == "HEAD" or _git_ref_exists(source_repo_root, ref):
            return ref
    return "HEAD"


def _prepare_discovery_repo(*, source_repo_root: Path, target: dict[str, Any]) -> Path:
    source_repo_root = source_repo_root.resolve()
    target_id = str(target.get("target_id") or source_repo_root.name or "target").strip()
    scan_root = (_discovery_worktrees_root() / _slug(target_id, default="target")).resolve()
    scan_root.parent.mkdir(parents=True, exist_ok=True)
    ref = _preferred_discovery_ref(source_repo_root=source_repo_root, target=target)

    def _recreate() -> None:
        try:
            subprocess.run(
                ["git", "-C", str(source_repo_root), "worktree", "remove", "--force", str(scan_root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=30,
            )
        except Exception:
            pass
        _remove_tree_force(scan_root)
        proc = subprocess.run(
            ["git", "-C", str(source_repo_root), "worktree", "add", "--force", "--detach", str(scan_root), ref],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise TeamWorkflowError(f"failed to prepare discovery worktree: {detail or 'git worktree add failed'}")

    if not scan_root.exists() or _git_dir(scan_root) is None:
        _recreate()

    checkout = subprocess.run(
        ["git", "-C", str(scan_root), "checkout", "--detach", ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=60,
    )
    if checkout.returncode != 0:
        _recreate()

    for args in (
        ["git", "-C", str(scan_root), "reset", "--hard", ref],
        ["git", "-C", str(scan_root), "clean", "-fdx"],
    ):
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=60,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise TeamWorkflowError(f"failed to sync discovery worktree: {detail or 'git reset/clean failed'}")
    return scan_root


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


def _lane_state(target_id: str, lane: str) -> dict[str, Any]:
    state = _read_state(target_id)
    lane_states = state.get("lane_states")
    if not isinstance(lane_states, dict):
        return {}
    lane_doc = lane_states.get(str(lane or "").strip().lower())
    return dict(lane_doc) if isinstance(lane_doc, dict) else {}


def _merge_lane_state(target_id: str, lane: str, payload: dict[str, Any]) -> dict[str, Any]:
    target_key = str(target_id or "").strip()
    lane_key = str(lane or "").strip().lower()
    state = _read_state(target_key)
    lane_states = dict(state.get("lane_states") or {}) if isinstance(state.get("lane_states"), dict) else {}
    current = dict(lane_states.get(lane_key) or {}) if isinstance(lane_states.get(lane_key), dict) else {}
    current.update(dict(payload or {}))
    lane_states[lane_key] = current
    state["lane_states"] = lane_states
    improvement_store.save_target_state(target_key, state)
    return current


def _bug_dormant_after_zero_scans(*, project_id: str) -> int:
    return crewai_workflow_registry.workflow_for_lane_phase("bug", crewai_workflow_registry.PHASE_FINDING, project_id=project_id).dormant_after_zero_scans()


def _has_unresolved_bug_tasks(*, project_id: str, target_id: str) -> bool:
    for task in improvement_store.list_delivery_tasks(project_id=project_id, target_id=target_id):
        su = dict(task.get("team_workflow") or {}) if isinstance(task.get("team_workflow"), dict) else {}
        orchestration = dict(task.get("orchestration") or {}) if isinstance(task.get("orchestration"), dict) else {}
        lane = str(su.get("lane") or orchestration.get("finding_lane") or "").strip().lower()
        status = str(task.get("status") or task.get("state") or "").strip().lower()
        if lane == "bug" and status != "closed":
            return True
    return False


def _bug_scan_policy(*, target_id: str, project_id: str, repo_context: dict[str, Any], force: bool) -> dict[str, Any]:
    threshold = _bug_dormant_after_zero_scans(project_id=project_id)
    lane_doc = _lane_state(target_id, "bug")
    current_head = str(repo_context.get("head_commit") or "").strip()
    previous_head = str(lane_doc.get("head_commit") or "").strip()
    current_status = str(lane_doc.get("status") or "active").strip().lower() or "active"
    unresolved_bug_tasks = _has_unresolved_bug_tasks(project_id=project_id, target_id=target_id)
    if force:
        return {
            "dormant": False,
            "reason": "force",
            "threshold": threshold,
            "head_commit": current_head,
            "previous_head_commit": previous_head,
            "unresolved_bug_tasks": unresolved_bug_tasks,
            "lane_state": lane_doc,
            "woke": current_status == "dormant",
        }
    if unresolved_bug_tasks:
        return {
            "dormant": False,
            "reason": "open_bug_tasks",
            "threshold": threshold,
            "head_commit": current_head,
            "previous_head_commit": previous_head,
            "unresolved_bug_tasks": True,
            "lane_state": lane_doc,
            "woke": current_status == "dormant",
        }
    if current_status == "dormant" and current_head == previous_head:
        return {
            "dormant": True,
            "reason": "unchanged_head_after_zero_bug_convergence",
            "threshold": threshold,
            "head_commit": current_head,
            "previous_head_commit": previous_head,
            "unresolved_bug_tasks": False,
            "lane_state": lane_doc,
            "woke": False,
        }
    if current_status == "dormant":
        return {
            "dormant": False,
            "reason": "head_changed",
            "threshold": threshold,
            "head_commit": current_head,
            "previous_head_commit": previous_head,
            "unresolved_bug_tasks": False,
            "lane_state": lane_doc,
            "woke": True,
        }
    return {
        "dormant": False,
        "reason": "active",
        "threshold": threshold,
        "head_commit": current_head,
        "previous_head_commit": previous_head,
        "unresolved_bug_tasks": False,
        "lane_state": lane_doc,
        "woke": False,
    }


def _update_bug_lane_state(
    *,
    db: Any,
    actor: str,
    target_id: str,
    project_id: str,
    workstream_id: str,
    repo_context: dict[str, Any],
    bug_finding_count: int,
    policy: dict[str, Any],
) -> dict[str, Any]:
    threshold = max(0, int(policy.get("threshold") or 0))
    current_head = str(policy.get("head_commit") or repo_context.get("head_commit") or "").strip()
    previous_doc = dict(policy.get("lane_state") or {})
    previous_status = str(previous_doc.get("status") or "active").strip().lower() or "active"
    previous_head = str(previous_doc.get("head_commit") or "").strip()
    unresolved_bug_tasks = bool(policy.get("unresolved_bug_tasks"))
    try:
        previous_streak = max(0, int(previous_doc.get("zero_bug_scan_streak") or 0))
    except Exception:
        previous_streak = 0
    now = _utc_now_iso()

    if unresolved_bug_tasks or int(bug_finding_count) > 0:
        zero_bug_scan_streak = 0
        status = "active"
        transition_reason = "open_bug_tasks" if unresolved_bug_tasks else "bug_findings_detected"
        dormant_since = ""
    else:
        zero_bug_scan_streak = 1 if current_head != previous_head else previous_streak + 1
        if threshold > 0 and zero_bug_scan_streak >= threshold:
            status = "dormant"
            transition_reason = "zero_bug_converged"
            dormant_since = str(previous_doc.get("dormant_since") or now)
        else:
            status = "active"
            transition_reason = "zero_bug_scan_streak"
            dormant_since = ""

    updated = _merge_lane_state(
        target_id,
        "bug",
        {
            "status": status,
            "head_commit": current_head,
            "zero_bug_scan_streak": zero_bug_scan_streak,
            "dormant_after_zero_scans": threshold,
            "last_bug_finding_count": int(bug_finding_count),
            "unresolved_bug_tasks": bool(unresolved_bug_tasks),
            "last_scan_at": now,
            "last_transition_reason": transition_reason,
            "last_transition_at": now,
            "dormant_since": dormant_since,
            "last_policy_reason": str(policy.get("reason") or ""),
        },
    )
    if previous_status != "dormant" and status == "dormant":
        db.add_event(
            event_type="TEAM_WORKFLOW_BUG_LANE_DORMANT",
            actor=actor,
            project_id=project_id,
            workstream_id=workstream_id,
            payload={
                "target_id": target_id,
                "head_commit": current_head,
                "zero_bug_scan_streak": zero_bug_scan_streak,
                "threshold": threshold,
            },
        )
    elif previous_status == "dormant" and status != "dormant":
        db.add_event(
            event_type="TEAM_WORKFLOW_BUG_LANE_RESUMED",
            actor=actor,
            project_id=project_id,
            workstream_id=workstream_id,
            payload={
                "target_id": target_id,
                "head_commit": current_head,
                "reason": str(policy.get("reason") or transition_reason),
            },
        )
    return updated


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


def collect_repo_context(
    *,
    repo_root: Path,
    explicit_repo_locator: str = "",
    target_id: str = "",
    scan_repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    scan_root = (scan_repo_root or repo_root).resolve()
    locator = str(explicit_repo_locator or "").strip()
    origin = _origin_url(repo_root) or _origin_url(scan_root)
    if not locator:
        locator = _parse_repo_locator(origin)
    branch, head_ref = _head_ref(scan_root)
    head_commit = ""
    gd = _git_dir(scan_root)
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
            ["git", "-C", str(scan_root), "status", "--porcelain"],
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
        readme = _read_text(scan_root / name, max_chars=3000)
        if readme:
            break

    dep_files = []
    for rel in ("pyproject.toml", "requirements.txt", "package.json", "setup.py", "setup.cfg"):
        if (scan_root / rel).exists():
            dep_files.append(rel)

    workflow_files = _sample_files(scan_root, ".github/workflows/*", limit=20)
    test_files = _sample_files(scan_root, "tests/test_*.py", limit=25)
    if not test_files:
        test_files = _sample_files(scan_root, "test_*.py", limit=25)

    top_level = []
    try:
        for child in sorted(scan_root.iterdir()):
            top_level.append(child.name + ("/" if child.is_dir() else ""))
            if len(top_level) >= 40:
                break
    except Exception:
        pass
    source_inventory = _source_inventory(scan_root)
    repository_inspection = _repository_inspection(scan_root)

    return {
        "repo_root": str(repo_root),
        "scan_repo_root": str(scan_root),
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
        "current_version": _read_current_version(scan_root),
        "recent_execution_metrics": _recent_execution_metrics(target_id=str(target_id or "").strip(), limit=8),
        "repository_inspection": repository_inspection,
        **source_inventory,
    }


def _codex_structured_model() -> str:
    return str(os.getenv("TEAMOS_LLM_MODEL") or "openai/gpt-5.4").strip()


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
    texts = [finding.title, finding.summary, finding.rationale, finding.why_not_covered, *list(finding.acceptance or [])]
    texts.extend([w.title for w in (finding.work_items or [])])
    texts.extend([w.summary for w in (finding.work_items or [])])
    texts.extend([w.why_not_covered for w in (finding.work_items or [])])
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
                "why_not_covered": str(finding.why_not_covered or ""),
                "work_items": [
                    {
                        "title": str(w.title or ""),
                        "summary": str(w.summary or ""),
                        "acceptance": list(w.acceptance or []),
                        "why_not_covered": str(w.why_not_covered or ""),
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
                    "why_not_covered": str(patch.why_not_covered or work_item.why_not_covered).strip() or str(work_item.why_not_covered or ""),
                }
            )
        )
    return finding.model_copy(
        update={
            "title": str(localized.title or finding.title).strip() or finding.title,
            "summary": str(localized.summary or finding.summary).strip() or finding.summary,
            "rationale": str(localized.rationale or finding.rationale).strip() or finding.rationale,
            "acceptance": [str(x).strip() for x in (localized.acceptance or finding.acceptance or []) if str(x).strip()],
            "why_not_covered": str(localized.why_not_covered or finding.why_not_covered).strip() or str(finding.why_not_covered or ""),
            "work_items": out_items,
        }
    )


def _localize_proposal_doc_to_zh(doc: dict[str, Any]) -> dict[str, Any]:
    if not _zh_localization_enabled():
        return dict(doc)
    texts = [doc.get("title"), doc.get("summary"), doc.get("rationale"), doc.get("why_not_covered")]
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
                "why_not_covered": str(doc.get("why_not_covered") or ""),
                "work_items": [
                    {
                        "title": str((x or {}).get("title") or ""),
                        "summary": str((x or {}).get("summary") or ""),
                        "acceptance": list(((x or {}).get("acceptance") or [])),
                        "why_not_covered": str((x or {}).get("why_not_covered") or ""),
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
    out["why_not_covered"] = str(localized.why_not_covered or doc.get("why_not_covered") or "").strip() or str(doc.get("why_not_covered") or "")
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
        if patch.why_not_covered:
            item["why_not_covered"] = str(patch.why_not_covered).strip()
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
    su = doc.get("team_workflow") or {}
    if not isinstance(su, dict):
        su = {}
    work_item = su.get("work_item") or {}
    if not isinstance(work_item, dict):
        work_item = {}
    texts = [doc.get("title"), su.get("summary"), su.get("rationale"), su.get("why_not_covered"), work_item.get("title"), work_item.get("summary"), work_item.get("why_not_covered")]
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
                "why_not_covered": str(work_item.get("why_not_covered") or su.get("why_not_covered") or ""),
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
    if localized.why_not_covered:
        su_out["why_not_covered"] = str(localized.why_not_covered).strip()
    wi_out = dict(work_item)
    if localized.title:
        wi_out["title"] = str(localized.title).strip()
    if localized.summary:
        wi_out["summary"] = str(localized.summary).strip()
    if localized.acceptance:
        wi_out["acceptance"] = [str(x).strip() for x in localized.acceptance if str(x).strip()]
    if localized.why_not_covered:
        wi_out["why_not_covered"] = str(localized.why_not_covered).strip()
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
    out["team_workflow"] = su_out
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
    return crewai_workflow_registry.workflow_for_lane_phase(lane, crewai_workflow_registry.PHASE_FINDING).default_version_bump


def _lane_default_cooldown_hours(lane: str, *, requires_user_confirmation: bool) -> int:
    _ = requires_user_confirmation
    return crewai_workflow_registry.workflow_for_lane_phase(lane, crewai_workflow_registry.PHASE_FINDING).cooldown_hours()


def _lane_default_baseline_action(lane: str, version_bump: str) -> str:
    return crewai_workflow_registry.workflow_for_lane_phase(lane, crewai_workflow_registry.PHASE_FINDING).default_baseline_action(version_bump)


def _default_proof_required(*, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> bool:
    lane = str(finding.lane or "").strip().lower()
    if lane == "bug":
        return True
    return bool(list(work_item.reproduction_steps or []) or list(work_item.test_case_files or []))


def _default_proof_bootstrap_required(*, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> bool:
    if not _default_proof_required(finding=finding, work_item=work_item):
        return False
    return not bool(list(work_item.test_case_files or []))


def _default_proof_failure_policy(*, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> str:
    if not _default_proof_required(finding=finding, work_item=work_item):
        return "block"
    lane = str(finding.lane or "").strip().lower()
    return "close" if lane == "bug" else "block"


def _default_approval_required(*, finding: UpgradeFinding) -> bool:
    return bool(finding.requires_user_confirmation)


def _default_approval_state(*, finding: UpgradeFinding, proposal_id: str) -> str:
    if not _default_approval_required(finding=finding):
        return "not_required"
    return "approved" if str(proposal_id or "").strip() else "pending"


def _build_coding_contract(
    *,
    finding: UpgradeFinding,
    work_item: UpgradeWorkItem,
    proposal_id: str,
    documentation_required: bool,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_doc = dict(existing or {})
    approval_doc = dict(existing_doc.get("approval") or {}) if isinstance(existing_doc.get("approval"), dict) else {}
    proof_doc = dict(existing_doc.get("proof") or {}) if isinstance(existing_doc.get("proof"), dict) else {}
    documentation_doc = dict(existing_doc.get("documentation") or {}) if isinstance(existing_doc.get("documentation"), dict) else {}
    return {
        "issue_type_hint": str(existing_doc.get("issue_type_hint") or finding.lane or "").strip().lower(),
        "approval": {
            "required": bool(approval_doc.get("required", _default_approval_required(finding=finding))),
            "state": str(approval_doc.get("state") or _default_approval_state(finding=finding, proposal_id=proposal_id)).strip() or _default_approval_state(finding=finding, proposal_id=proposal_id),
            "source": str(approval_doc.get("source") or ("proposal" if str(proposal_id or "").strip() else "direct_task")).strip() or "direct_task",
        },
        "proof": {
            "required": bool(proof_doc.get("required", _default_proof_required(finding=finding, work_item=work_item))),
            "bootstrap_if_missing": bool(proof_doc.get("bootstrap_if_missing", _default_proof_bootstrap_required(finding=finding, work_item=work_item))),
            "failure_policy": str(proof_doc.get("failure_policy") or _default_proof_failure_policy(finding=finding, work_item=work_item)).strip() or _default_proof_failure_policy(finding=finding, work_item=work_item),
        },
        "documentation": {
            "required": bool(documentation_doc.get("required", documentation_required)),
        },
    }


def _coding_contract(
    doc: dict[str, Any],
    *,
    finding: UpgradeFinding | None = None,
    work_item: UpgradeWorkItem | None = None,
    proposal_id: str = "",
    documentation_required: bool | None = None,
) -> dict[str, Any]:
    existing = doc.get("coding_contract")
    if isinstance(existing, dict) and existing:
        return _build_coding_contract(
            finding=finding or UpgradeFinding(kind="TASK", lane=str(existing.get("issue_type_hint") or "bug"), title="", summary=""),
            work_item=work_item or UpgradeWorkItem(title=""),
            proposal_id=proposal_id,
            documentation_required=bool((existing.get("documentation") or {}).get("required", documentation_required or False)),
            existing=existing,
        )
    if finding is None:
        su = _team_section(doc, key="team_workflow")
        lane = str(su.get("lane") or ((doc.get("orchestration") or {}) if isinstance(doc.get("orchestration"), dict) else {}).get("finding_lane") or "bug").strip().lower() or "bug"
        finding = UpgradeFinding(
            kind=str(su.get("kind") or "TASK"),
            lane=lane,
            title=str(doc.get("title") or ""),
            summary=str(su.get("summary") or ""),
            requires_user_confirmation=bool(su.get("requires_user_confirmation") or False),
        )
    if work_item is None:
        su = _team_section(doc, key="team_workflow")
        raw_work_item = su.get("work_item") or {}
        if isinstance(raw_work_item, dict):
            work_item = UpgradeWorkItem.model_validate({"title": str(raw_work_item.get("title") or doc.get("title") or "task"), **raw_work_item})
        else:
            work_item = UpgradeWorkItem(title=str(doc.get("title") or "task"))
    if documentation_required is None:
        documentation_required = bool((doc.get("documentation_policy") or {}).get("required", False)) if isinstance(doc.get("documentation_policy"), dict) else False
    normalized_proposal_id = str(proposal_id or ((doc.get("orchestration") or {}) if isinstance(doc.get("orchestration"), dict) else {}).get("proposal_id") or "").strip()
    return _build_coding_contract(
        finding=finding,
        work_item=work_item,
        proposal_id=normalized_proposal_id,
        documentation_required=bool(documentation_required),
        existing={},
    )


def _coding_owner_role(lane: str) -> str:
    _ = lane
    return ROLE_CODING_AGENT


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
            reproduction_steps=[],
            test_case_files=[],
            verification_steps=list(finding.acceptance or []),
            test_gap_type=str(finding.test_gap_type or "").strip().lower(),
            target_paths=[str(x).strip() for x in (finding.target_paths or []) if str(x).strip()],
            missing_paths=[str(x).strip() for x in (finding.missing_paths or []) if str(x).strip()],
            suggested_test_files=[str(x).strip() for x in (finding.suggested_test_files or []) if str(x).strip()],
            why_not_covered=str(finding.why_not_covered or "").strip(),
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


def _crewai_llm(*, workflow: Any | None = None):
    return crewai_llm_factory.build_crewai_llm(workflow=workflow)


def _coerce_plan(raw_output: Any, *, max_findings: int, repo_root: Path, current_version: str, project_id: str = "teamos") -> UpgradePlan:
    obj: Any = None
    if isinstance(raw_output, dict):
        obj = raw_output
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
            raise TeamWorkflowError("CrewAI returned no structured team workflow plan")
        plan = UpgradePlan.model_validate(json.loads(match.group(0)))

    findings: list[UpgradeFinding] = []
    lane_counts: dict[str, int] = {}
    findings_limit = max(0, int(max_findings))
    for finding in plan.findings:
        if findings_limit > 0 and len(findings) >= findings_limit:
            break
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
        lane_limit = _lane_max_candidates(lane, project_id=project_id)
        if lane_limit > 0 and int(lane_counts.get(lane, 0)) >= lane_limit:
            continue
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
                    reproduction_steps=[str(x).strip() for x in (getattr(item, "reproduction_steps", None) or []) if str(x).strip()][:10],
                    test_case_files=[str(x).strip() for x in (getattr(item, "test_case_files", None) or []) if str(x).strip()][:10],
                    verification_steps=[str(x).strip() for x in (getattr(item, "verification_steps", None) or getattr(item, "acceptance", None) or finding.acceptance or []) if str(x).strip()][:10],
                    test_gap_type=str(getattr(item, "test_gap_type", "") or getattr(finding, "test_gap_type", "") or "").strip().lower(),
                    target_paths=[str(x).strip() for x in (getattr(item, "target_paths", None) or getattr(finding, "target_paths", None) or []) if str(x).strip()][:20],
                    missing_paths=[str(x).strip() for x in (getattr(item, "missing_paths", None) or getattr(finding, "missing_paths", None) or []) if str(x).strip()][:20],
                    suggested_test_files=[str(x).strip() for x in (getattr(item, "suggested_test_files", None) or getattr(finding, "suggested_test_files", None) or []) if str(x).strip()][:20],
                    why_not_covered=str(getattr(item, "why_not_covered", "") or getattr(finding, "why_not_covered", "") or "").strip(),
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
            test_gap_type=str(getattr(finding, "test_gap_type", "") or "").strip().lower(),
            target_paths=[str(x).strip() for x in (getattr(finding, "target_paths", None) or []) if str(x).strip()][:20],
            missing_paths=[str(x).strip() for x in (getattr(finding, "missing_paths", None) or []) if str(x).strip()][:20],
            suggested_test_files=[str(x).strip() for x in (getattr(finding, "suggested_test_files", None) or []) if str(x).strip()][:20],
            why_not_covered=str(getattr(finding, "why_not_covered", "") or "").strip(),
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
        lane_counts[lane] = int(lane_counts.get(lane, 0)) + 1
    return UpgradePlan(
        summary=str(plan.summary or "").strip() or "CrewAI team workflow analysis completed.",
        findings=findings,
        ci_actions=[str(x).strip() for x in (plan.ci_actions or []) if str(x).strip()][:20],
        notes=[str(x).strip() for x in (plan.notes or []) if str(x).strip()][:20],
        current_version=current_version,
        planned_version=_planned_version(current_version, findings),
    )


def _scan_limit(max_findings: int, lane_limit: int) -> int:
    normalized_max = max(0, int(max_findings))
    normalized_lane = max(0, int(lane_limit))
    if normalized_max <= 0 and normalized_lane <= 0:
        return 0
    if normalized_max <= 0:
        return normalized_lane
    if normalized_lane <= 0:
        return normalized_max
    return min(normalized_max, normalized_lane)


def _structured_bug_scan_prompt(
    *,
    repo_context: dict[str, Any],
    module_chunk: dict[str, Any],
    bug_scan_limit: int,
    bug_scan_dormant: bool,
) -> str:
    inspection = repo_context.get("repository_inspection") if isinstance(repo_context.get("repository_inspection"), dict) else {}
    baseline_checks = inspection.get("baseline_checks") if isinstance(inspection, dict) else []
    baseline_summary = [
        {
            "command": str(item.get("command") or ""),
            "status": str(item.get("status") or ""),
            "returncode": item.get("returncode"),
            "stdout_tail": _tail_text(str(item.get("stdout") or ""), max_chars=300),
            "stderr_tail": _tail_text(str(item.get("stderr") or ""), max_chars=300),
        }
        for item in (baseline_checks or [])
        if isinstance(item, dict)
    ][:3]
    module_payload = {
        "module": module_chunk.get("module"),
        "files": [
            {
                "path": str(item.get("path") or ""),
                "category": str(item.get("category") or "other"),
                "bytes": item.get("bytes"),
            }
            for item in (module_chunk.get("files") or [])
            if isinstance(item, dict)
        ],
    }
    shared_context = {
        "repo_name": repo_context.get("repo_name"),
        "repo_locator": repo_context.get("repo_locator"),
        "head_commit": repo_context.get("head_commit"),
    }
    payload = {
        "shared_context": shared_context,
        "baseline_checks": baseline_summary,
        "module_chunk": module_payload,
    }
    module = str(module_chunk.get("module") or "module").strip()
    dormant_note = (
        "当前 bug lane 处于 dormant 状态。只有在输入里存在明确当前失败信号时，才允许输出 bug finding。"
        if bug_scan_dormant
        else ""
    )
    return "\n".join(
        [
            "你是 Team OS 的 Test-Manager。",
            f"请按模块完整通读 `{module}`，并使用仓库工具自行读取源码和测试文件，判断当前是否存在可证明的 bug。",
            "要求：",
            "- bug finding 数量不设上限；如果当前没有可证明缺陷，返回 0 个是完全正常的结果。",
            "- 输入 JSON 只提供模块索引；源码和测试内容请通过工具自行读取，不要依赖输入 JSON 里的文件内容。",
            "- 只有当存在当前失败行为、测试失败、异常栈、可执行复现路径，或可归因到该模块的 baseline check 失败/超时时，才允许输出 bug。",
            "- 缺测试、未覆盖路径、代码味道、潜在风险、推测性的设计问题，都不是 bug；这些应当留给 quality/test-gap 流程。",
            "- 环境缺工具这类失败（例如 make 不存在）不是仓库 bug；应放到 ci_actions 或 notes。",
            "- 每个 finding 只返回最小 bug 候选字段：title、summary、rationale、impact、module、files、tests、acceptance、reproduction_steps、test_case_files、verification_steps。",
            "- 不要返回 work_items、owner_role、review_role、qa_role、lane、kind、version_bump 这类流程字段；这些由运行时补全。",
            "- 所有面向用户的自然语言字段必须使用简体中文。",
            "- 只输出符合给定 JSON Schema 的对象。",
            dormant_note,
            "",
            "输入 JSON：",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    ).strip()


def _prompt_safe_repository_inspection(inspection: dict[str, Any]) -> dict[str, Any]:
    text_pass = inspection.get("text_pass") if isinstance(inspection.get("text_pass"), dict) else {}
    return {
        "tracked_file_count": inspection.get("tracked_file_count"),
        "tracked_file_sample": inspection.get("tracked_file_sample"),
        "top_level_directory_counts": inspection.get("top_level_directory_counts"),
        "category_counts": inspection.get("category_counts"),
        "test_command_candidates": inspection.get("test_command_candidates"),
        "focus_file_index": [
            {
                "path": str(item.get("path") or ""),
                "bytes": item.get("bytes"),
            }
            for item in (inspection.get("focus_file_excerpts") or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ][:20],
        "text_pass_summary": {
            "text_file_count": text_pass.get("text_file_count"),
            "marker_totals": text_pass.get("marker_totals"),
            "suspicious_files": text_pass.get("suspicious_files"),
        },
        "module_chunk_index": [
            {
                "module": str(item.get("module") or ""),
                "included_file_count": item.get("included_file_count"),
                "path_sample": item.get("path_sample"),
            }
            for item in (inspection.get("module_chunks") or [])
            if isinstance(item, dict)
        ][:20],
    }


def _prompt_safe_repo_context(repo_context: dict[str, Any]) -> dict[str, Any]:
    inspection = repo_context.get("repository_inspection") if isinstance(repo_context.get("repository_inspection"), dict) else {}
    return {
        "repo_name": repo_context.get("repo_name"),
        "repo_locator": repo_context.get("repo_locator"),
        "default_branch": repo_context.get("default_branch"),
        "head_commit": repo_context.get("head_commit"),
        "current_version": repo_context.get("current_version"),
        "git_status_dirty": repo_context.get("git_status_dirty"),
        "git_status_sample": repo_context.get("git_status_sample"),
        "top_level_entries": repo_context.get("top_level_entries"),
        "dependency_files": repo_context.get("dependency_files"),
        "workflow_files": repo_context.get("workflow_files"),
        "test_files": repo_context.get("test_files"),
        "has_github_actions": repo_context.get("has_github_actions"),
        "recent_execution_metrics": repo_context.get("recent_execution_metrics"),
        "repository_inspection": _prompt_safe_repository_inspection(inspection),
    }


def _build_bug_scan_tools(*, repo_root: Path, repo_context: dict[str, Any]) -> dict[str, list[Any]]:
    from app.domains.team_workflow import task_runtime as delivery

    inspection = repo_context.get("repository_inspection") if isinstance(repo_context.get("repository_inspection"), dict) else {}
    tests_allowlist = [str(x).strip() for x in (inspection.get("test_command_candidates") or []) if str(x).strip()]
    base_tools = delivery._build_repo_tools(repo_root=repo_root, allowed_paths=["."], tests_allowlist=tests_allowlist)
    read_tools = list(base_tools.get("read") or [])
    qa_tools: list[Any] = list(read_tools)
    for tool_obj in list(base_tools.get("qa") or []):
        tool_name = str(getattr(tool_obj, "name", "") or "").strip()
        if any(str(getattr(existing, "name", "") or "").strip() == tool_name for existing in qa_tools):
            continue
        qa_tools.append(tool_obj)
    return {"read": read_tools, "qa": qa_tools}


def _structured_bug_scan_repo_prompt(
    *,
    repo_context: dict[str, Any],
    bug_scan_limit: int,
    bug_scan_dormant: bool,
) -> str:
    inspection = repo_context.get("repository_inspection") if isinstance(repo_context.get("repository_inspection"), dict) else {}
    baseline_checks = inspection.get("baseline_checks") if isinstance(inspection.get("baseline_checks"), list) else []
    baseline_summary = [
        {
            "command": str(item.get("command") or ""),
            "status": str(item.get("status") or ""),
            "returncode": item.get("returncode"),
            "stdout_tail": _tail_text(str(item.get("stdout") or ""), max_chars=300),
            "stderr_tail": _tail_text(str(item.get("stderr") or ""), max_chars=300),
        }
        for item in baseline_checks
        if isinstance(item, dict)
    ][:6]
    inspection_summary = _prompt_safe_repository_inspection(inspection)
    payload = {
        "shared_context": {
            "repo_name": repo_context.get("repo_name"),
            "repo_locator": repo_context.get("repo_locator"),
            "head_commit": repo_context.get("head_commit"),
        },
        "inspection_summary": inspection_summary,
        "baseline_checks": baseline_summary,
    }
    dormant_note = (
        "当前 bug lane 处于 dormant 状态。只有在你通过仓库读取和验证确认了明确当前失败信号时，才允许输出 bug finding。"
        if bug_scan_dormant
        else ""
    )
    return "\n".join(
        [
            "你是 Team OS 的 Test-Manager。",
            "你现在拥有整个仓库的只读扫描权限。请自行决定先读哪些目录、哪些源码文件、哪些测试文件，并使用工具完成整仓 bug triage。",
            "要求：",
            "- bug finding 数量不设上限；如果当前没有可证明缺陷，返回 0 个是完全正常的结果。",
            "- 你必须主动使用仓库工具进行检查，而不是只复述输入摘要。",
            "- 你应优先结合目录结构、搜索结果、源码文件、测试文件、工作流文件和 baseline check 结果来判断 bug。",
            "- 输入 JSON 只提供仓库摘要；源码和测试内容请通过工具自行读取，不要依赖输入 JSON 里的文件内容。",
            "- 只有当存在当前失败行为、测试失败、异常栈、可执行复现路径、或能从当前代码直接证明的高置信静态缺陷时，才允许输出 bug。",
            "- 缺测试、未覆盖路径、代码味道、潜在风险、推测性的设计问题，都不是 bug；这些应当留给 quality/test-gap 流程。",
            "- 环境缺工具这类失败（例如 make 不存在）不是仓库 bug；应放到 ci_actions 或 notes。",
            "- 每个 finding 只返回最小 bug 候选字段：title、summary、rationale、impact、module、files、tests、acceptance、reproduction_steps、test_case_files、verification_steps。",
            "- 不要返回 work_items、owner_role、review_role、qa_role、lane、kind、version_bump 这类流程字段；这些由运行时补全。",
            "- 所有面向用户的自然语言字段必须使用简体中文。",
            "- 只输出符合给定 JSON Schema 的对象。",
            dormant_note,
            "",
            "你可以使用的工具：",
            "- List Directory：浏览目录",
            "- Search Repository：全文搜索仓库",
            "- Read Repository File：读取文件全文",
            "- Git Status / Git Diff：查看当前仓库状态",
            "- Run Validation Command：执行安全测试命令",
            "",
            "输入 JSON：",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    ).strip()


def _coerce_structured_task_output(raw_output: Any, model_cls: type[BaseModel], *, error_text: str) -> BaseModel:
    obj: Any = None
    if hasattr(raw_output, "to_dict"):
        try:
            obj = raw_output.to_dict()
        except Exception:
            obj = None
    if obj is None and hasattr(raw_output, "json_dict"):
        try:
            obj = raw_output.json_dict
        except Exception:
            obj = None
    if obj is None and hasattr(raw_output, "pydantic"):
        obj = getattr(raw_output, "pydantic", None)
    if obj is None:
        obj = raw_output
    if isinstance(obj, model_cls):
        return obj
    if isinstance(obj, dict):
        return model_cls.model_validate(obj)
    text = str(getattr(raw_output, "raw", "") or str(raw_output or "")).strip()
    try:
        return model_cls.model_validate(json.loads(text))
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise RuntimeError(error_text)
        return model_cls.model_validate(json.loads(match.group(0)))


def _structured_bug_scan_for_repo(
    *,
    team_id: str = "",
    repo_context: dict[str, Any],
    bug_scan_limit: int,
    bug_scan_dormant: bool,
    verbose: bool,
) -> tuple[StructuredBugScanResult, dict[str, Any]]:
    from crewai import Crew, Process, Task

    repo_root = Path(str(repo_context.get("scan_repo_root") or repo_context.get("repo_root") or ".")).resolve()
    llm = _crewai_llm()
    tools_by_profile = _build_bug_scan_tools(repo_root=repo_root, repo_context=repo_context)
    agent = crewai_agent_factory.build_crewai_agent(
        role_id=ROLE_TEST_MANAGER,
        team_id=_normalize_team_id(team_id),
        llm=llm,
        verbose=verbose,
        tools_by_profile=tools_by_profile,
        tool_profile="qa",
    )
    task = Task(
        name="qa_bug_scan_repo",
        description=_structured_bug_scan_repo_prompt(
            repo_context=repo_context,
            bug_scan_limit=bug_scan_limit,
            bug_scan_dormant=bug_scan_dormant,
        ),
        expected_output="A structured JSON bug triage result for the whole repository.",
        agent=agent,
        output_json=StructuredBugScanResult,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=verbose)
    with crewai_runtime.suppress_proxy_for_codex_oauth(model=str(getattr(llm, "model", "") or "")):
        out = crew.kickoff()
    parsed = _coerce_structured_task_output(
        out,
        StructuredBugScanResult,
        error_text="CrewAI returned no structured output for repo bug scan",
    )
    task_outputs = [
        {
            "name": str(getattr(t, "name", "") or ""),
            "agent": str(getattr(t, "agent", "") or ""),
            "raw": str(getattr(t, "raw", "") or "")[:4000],
        }
        for t in (getattr(out, "tasks_output", None) or [])
    ]
    if not task_outputs:
        task_outputs = [
            {
                "name": "qa_bug_scan_repo",
                "agent": ROLE_TEST_MANAGER,
                "raw": json.dumps(parsed.model_dump(), ensure_ascii=False, indent=2)[:4000],
            }
        ]
    return parsed, task_outputs[0]


def _structured_bug_scan_for_chunk(
    *,
    llm: Any,
    repo_context: dict[str, Any],
    module_chunk: dict[str, Any],
    bug_scan_limit: int,
    bug_scan_dormant: bool,
) -> tuple[StructuredBugScanResult, dict[str, Any]]:
    prompt = _structured_bug_scan_prompt(
        repo_context=repo_context,
        module_chunk=module_chunk,
        bug_scan_limit=bug_scan_limit,
        bug_scan_dormant=bug_scan_dormant,
    )
    with crewai_runtime.suppress_proxy_for_codex_oauth(model=str(getattr(llm, "model", "") or "")):
        raw_result = llm.call(prompt, response_model=StructuredBugScanResult)
    parsed = raw_result if isinstance(raw_result, StructuredBugScanResult) else StructuredBugScanResult.model_validate(raw_result)
    module = str(module_chunk.get("module") or "module").strip()
    return parsed, {
        "name": f"qa_bug_scan_{_module_slug(module)}",
        "agent": ROLE_TEST_MANAGER,
        "raw": json.dumps(parsed.model_dump(), ensure_ascii=False, indent=2)[:4000],
    }


def _kickoff_bug_only_plan(
    *,
    team_id: str = "",
    repo_context: dict[str, Any],
    project_id: str,
    max_findings: int,
    bug_scan_limit: int,
    bug_scan_dormant: bool,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[UpgradePlan, dict[str, Any]]:
    repo_root = Path(str(repo_context.get("repo_root") or ".")).resolve()
    current_version = str(repo_context.get("current_version") or "0.1.0").strip() or "0.1.0"
    task_outputs: list[dict[str, Any]] = []
    aggregated_findings: list[dict[str, Any]] = []
    ci_actions: list[str] = []
    notes: list[str] = []
    summaries: list[str] = []
    findings_limit = max(0, int(max_findings))

    scan_result, debug_task = _structured_bug_scan_for_repo(
        team_id=team_id,
        repo_context=repo_context,
        bug_scan_limit=max(1, int(bug_scan_limit or 0)),
        bug_scan_dormant=bug_scan_dormant,
        verbose=False,
    )
    task_outputs.append(debug_task)
    if str(scan_result.summary or "").strip():
        summaries.append(str(scan_result.summary).strip())
    ci_actions.extend([str(x).strip() for x in (scan_result.ci_actions or []) if str(x).strip()])
    notes.extend([str(x).strip() for x in (scan_result.notes or []) if str(x).strip()])
    for candidate in list(scan_result.findings or []):
        title = str(candidate.title or "").strip()
        summary = str(candidate.summary or "").strip()
        if not title and not summary:
            continue
        files = [str(x).strip() for x in (candidate.files or []) if str(x).strip()][:20]
        tests = [str(x).strip() for x in (candidate.tests or []) if str(x).strip()][:20]
        acceptance = [str(x).strip() for x in (candidate.acceptance or []) if str(x).strip()][:20]
        reproduction_steps = [str(x).strip() for x in (candidate.reproduction_steps or []) if str(x).strip()][:10]
        test_case_files = [str(x).strip() for x in (candidate.test_case_files or []) if str(x).strip()][:10]
        verification_steps = [str(x).strip() for x in (candidate.verification_steps or []) if str(x).strip()][:10]
        module = _normalize_module_name(
            str(candidate.module or "").strip(),
            paths=files,
            workstream_id="general",
            title=title,
            summary=summary,
            lane="bug",
        )
        aggregated_findings.append(
            {
                "kind": "BUG",
                "lane": "bug",
                "title": title or summary or "未命名缺陷",
                "summary": summary or title or "发现可证明的当前缺陷信号。",
                "module": module,
                "rationale": str(candidate.rationale or "").strip(),
                "impact": str(candidate.impact or "MED").strip() or "MED",
                "workstream_id": "general",
                "files": files,
                "tests": tests,
                "acceptance": acceptance,
                "version_bump": "patch",
                "requires_user_confirmation": False,
                "work_items": [
                    {
                        "title": title or summary or "未命名缺陷",
                        "summary": summary or title or "发现可证明的当前缺陷信号。",
                        "owner_role": ROLE_CODING_AGENT,
                        "review_role": ROLE_REVIEW_AGENT,
                        "qa_role": ROLE_QA_AGENT,
                        "workstream_id": "general",
                        "allowed_paths": files,
                        "tests": tests,
                        "acceptance": acceptance,
                        "reproduction_steps": reproduction_steps,
                        "test_case_files": test_case_files,
                        "verification_steps": verification_steps or acceptance,
                        "module": module,
                    }
                ],
            }
        )
        if findings_limit > 0 and len(aggregated_findings) >= findings_limit:
            break
    if progress_callback is not None:
        try:
            progress_callback(
                {
                    "module": "repository",
                    "summary": "；".join([x for x in summaries if x]).strip() or str(scan_result.summary or "").strip(),
                    "findings": list(aggregated_findings),
                    "ci_actions": ci_actions[:20],
                    "notes": notes[:20],
                    "current_version": current_version,
                    "planned_version": current_version,
                    "crew_debug": {"task_outputs": list(task_outputs)},
                }
            )
        except Exception:
            pass
    if aggregated_findings:
        notes.append("已完成整仓 bug 通读扫描；本轮将把已验证缺陷统一进入 issue/task materialize。")

    raw_plan = {
        "summary": "；".join([x for x in summaries if x]).strip() or "Bug-only team workflow analysis completed.",
        "findings": aggregated_findings[:findings_limit] if findings_limit > 0 else aggregated_findings,
        "ci_actions": ci_actions[:20],
        "notes": notes[:20],
        "current_version": current_version,
        "planned_version": current_version,
    }
    plan = _coerce_plan(
        raw_plan,
        max_findings=max_findings,
        repo_root=repo_root,
        current_version=current_version,
        project_id=project_id,
    )
    plan = plan.model_copy(update={"findings": [_localize_finding_to_zh(f) for f in (plan.findings or [])]})
    task_outputs.append(
        {
            "name": "structured_bug_plan",
            "agent": "Team-Workflow-Bug-Planner",
            "raw": json.dumps(raw_plan, ensure_ascii=False, indent=2)[:4000],
        }
    )
    return plan, {
        "raw": json.dumps(raw_plan, ensure_ascii=False, indent=2),
        "token_usage": {},
        "task_outputs": task_outputs,
    }


def kickoff_upgrade_plan(
    *,
    team_id: str = "",
    repo_context: dict[str, Any],
    project_id: str = "teamos",
    max_findings: int,
    verbose: bool = False,
    bug_scan_dormant: bool = False,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[UpgradePlan, dict[str, Any]]:
    repo_blob = json.dumps(_prompt_safe_repo_context(repo_context), ensure_ascii=False, indent=2)
    feature_scan_limit = _scan_limit(max_findings, _lane_max_candidates("feature", project_id=project_id))
    bug_scan_limit = _scan_limit(max_findings, _lane_max_candidates("bug", project_id=project_id))
    quality_scan_limit = _scan_limit(max_findings, _lane_max_candidates("quality", project_id=project_id))
    enabled_workflows = _enabled_planning_workflow_ids(project_id=project_id)
    feature_enabled = crewai_role_registry.WORKFLOW_FEATURE_FINDING in enabled_workflows
    bug_enabled = crewai_role_registry.WORKFLOW_BUG_FINDING in enabled_workflows
    quality_enabled = crewai_role_registry.WORKFLOW_QUALITY_FINDING in enabled_workflows
    process_enabled = crewai_role_registry.WORKFLOW_PROCESS_FINDING in enabled_workflows

    if _use_bug_only_fast_path(project_id=project_id, enabled_workflows=enabled_workflows):
        return _kickoff_bug_only_plan(
            team_id=team_id,
            repo_context=repo_context,
            project_id=project_id,
            max_findings=max_findings,
            bug_scan_limit=bug_scan_limit,
            bug_scan_dormant=bug_scan_dormant,
            progress_callback=progress_callback,
        )

    crewai_runtime.require_crewai_importable()
    from crewai import Crew, Process, Task

    llm = _crewai_llm()

    normalized_team_id = _normalize_team_id(team_id)
    issue_drafter = crewai_agent_factory.build_crewai_agent(role_id=ROLE_ISSUE_DRAFTER, team_id=normalized_team_id, llm=llm, verbose=verbose)
    review_agent = crewai_agent_factory.build_crewai_agent(role_id=ROLE_PLAN_REVIEW_AGENT, team_id=normalized_team_id, llm=llm, verbose=verbose)
    qa_agent = crewai_agent_factory.build_crewai_agent(role_id=ROLE_PLAN_QA_AGENT, team_id=normalized_team_id, llm=llm, verbose=verbose)
    enabled_agents: list[Any] = [issue_drafter, review_agent, qa_agent]
    enabled_scan_tasks: list[Any] = []

    if feature_enabled:
        product_manager = crewai_agent_factory.build_crewai_agent(role_id=ROLE_PRODUCT_MANAGER, team_id=normalized_team_id, llm=llm, verbose=verbose)
        enabled_agents.append(product_manager)
        enabled_scan_tasks.append(
            Task(
                name="product_feature_scan",
                description=(
                    (
                        "Analyze the repository context as a product manager.\n"
                        + (
                            f"Return at most {int(feature_scan_limit)} feature ideas or product optimizations.\n"
                            if feature_scan_limit > 0
                            else "Return every actionable feature idea or product optimization you can prove from the repository context.\n"
                        )
                    )
                    +
                    "Mark changes that should be treated as FEATURE and note whether they imply a major or minor version bump.\n"
                    "Use only the supplied context.\n\n"
                    f"Repository context:\n{repo_blob}"
                ),
                expected_output="A concise shortlist of feature candidates with evidence and release impact.",
                agent=product_manager,
                markdown=True,
            )
        )
    if bug_enabled:
        bug_scan_tools = _build_bug_scan_tools(
            repo_root=Path(str(repo_context.get("scan_repo_root") or repo_context.get("repo_root") or ".")).resolve(),
            repo_context=repo_context,
        )
        test_manager = crewai_agent_factory.build_crewai_agent(
            role_id=ROLE_TEST_MANAGER,
            team_id=normalized_team_id,
            llm=llm,
            verbose=verbose,
            tools_by_profile=bug_scan_tools,
            tool_profile="qa",
        )
        enabled_agents.append(test_manager)
        enabled_scan_tasks.append(
            Task(
                name="qa_bug_scan_repo",
                description=_structured_bug_scan_repo_prompt(
                    repo_context=repo_context,
                    bug_scan_limit=max(1, int(bug_scan_limit or 0)),
                    bug_scan_dormant=bug_scan_dormant,
                ),
                expected_output="A structured repository-wide bug triage result backed by active repository inspection.",
                agent=test_manager,
                output_json=StructuredBugScanResult,
                markdown=True,
            )
        )
    if quality_enabled:
        test_case_gap_agent = crewai_agent_factory.build_crewai_agent(role_id=ROLE_TEST_CASE_GAP_AGENT, team_id=normalized_team_id, llm=llm, verbose=verbose)
        code_quality_analyst = crewai_agent_factory.build_crewai_agent(role_id=ROLE_CODE_QUALITY_ANALYST, team_id=normalized_team_id, llm=llm, verbose=verbose)
        enabled_agents.extend([test_case_gap_agent, code_quality_analyst])
        enabled_scan_tasks.extend(
            [
                Task(
                    name="qa_test_gap_scan",
                    description=(
                        (
                            "Analyze the repository context as a dedicated test-case gap agent.\n"
                            + (
                                f"Return at most {int(quality_scan_limit)} high-value black-box or white-box test gap candidates.\n"
                                if quality_scan_limit > 0
                                else "Return every high-value black-box or white-box test gap candidate you can prove from the repository context.\n"
                            )
                        )
                        +
                        "Only propose a candidate when there is a clearly identifiable untested path, missing regression protection, or behavior/branch coverage gap worth tracking as an issue.\n"
                        "For each candidate, distinguish test_gap_type as blackbox or whitebox, identify target_paths and missing_paths, suggest repo-relative suggested_test_files, and explain why the path is not covered today.\n"
                        "Use lane=quality and kind=CODE_QUALITY for these findings.\n"
                        "Use only the supplied context.\n\n"
                        f"Repository context:\n{repo_blob}"
                    ),
                    expected_output="A shortlist of blackbox/whitebox test gap findings with uncovered paths and suggested test files.",
                    agent=test_case_gap_agent,
                    markdown=True,
                ),
                Task(
                    name="code_quality_scan",
                    description=(
                        (
                            "Review the repository context as a code quality analyst.\n"
                            + (
                                f"Return at most {int(quality_scan_limit)} code quality candidates.\n"
                                if quality_scan_limit > 0
                                else "Return every concrete, high-value code quality candidate you can support with repository evidence.\n"
                            )
                        )
                        +
                        "Focus on duplicated logic, unnecessary files, dead/stale code candidates, oversized modules, and reuse/refactor opportunities.\n"
                        "Do not spend candidates on pure test-gap discovery; that is handled by the dedicated test-case gap scan.\n"
                        "Only propose work when the quality gain is concrete and the change can be broken into small, scoped items.\n"
                        "Use only the supplied context.\n\n"
                        f"Repository context:\n{repo_blob}"
                    ),
                    expected_output="A shortlist of code quality findings with evidence, cleanup value, and safe refactor boundaries.",
                    agent=code_quality_analyst,
                    markdown=True,
                ),
            ]
        )
    if process_enabled:
        process_analyst = crewai_agent_factory.build_crewai_agent(role_id=ROLE_PROCESS_OPTIMIZATION_ANALYST, team_id=normalized_team_id, llm=llm, verbose=verbose)
        enabled_agents.append(process_analyst)
        enabled_scan_tasks.append(
            Task(
                name="process_optimization_scan",
                description=(
                    "Review the repository context and recent team workflow execution telemetry.\n"
                    "Identify process improvements only if they are grounded in recent failures, delays, or workflow friction.\n"
                    "Prefer one high-value process improvement over many weak ones."
                ),
                expected_output="A short list of process improvements grounded in run history and telemetry.",
                agent=process_analyst,
                markdown=True,
            )
        )

    plan_task = Task(
        name="draft_execution_backlog",
        description=(
            "Transform the feature scan, bug scan, test-gap scan, code quality scan, and process scan into an actionable upgrade backlog.\n"
            "Output JSON matching UpgradePlan.\n"
            "Rules:\n"
            "- Features use lane=feature, kind=FEATURE, require user confirmation, and use version_bump=major or minor.\n"
            "- Bugs use lane=bug, kind=BUG, no user confirmation, and use version_bump=patch.\n"
            "- It is valid for the final plan to contain 0 bug findings when there is no current, provable defect signal.\n"
            "- Never create a bug from speculation, code smell, or missing test coverage alone.\n"
            "- Bug conclusions must consider repository_inspection, not just recent_execution_metrics. If the repository contains substantial source or test files, treat the inspection summary and file excerpts as primary evidence.\n"
            "- repository_inspection.text_pass reflects a repository-wide text read. Use it to ground module selection, test-gap context, and code-area rationale, but do not turn marker hits alone into bugs.\n"
            "- If repository_inspection.baseline_checks contains a failed or timed-out repo-attributable command, include that evidence explicitly in bug findings or ci_actions.\n"
            "- Code quality improvements use lane=quality, kind=CODE_QUALITY, require user confirmation, default to version_bump=none, and focus on cleanup/refactor/reuse/deletion work.\n"
            "- Test-gap findings also use lane=quality, kind=CODE_QUALITY, require user confirmation, and must set test_gap_type=blackbox or test_gap_type=whitebox.\n"
            "- Test-gap findings and work_items should carry target_paths, missing_paths, suggested_test_files, and why_not_covered so downstream issues can explain the exact uncovered path.\n"
            "- Process improvements use lane=process, kind=PROCESS, cooldown_hours=24, and version_bump=none.\n"
            "- Every finding must carry exactly one stable module name. Prefer one of: Runtime, Team-Workflow, CI, Doctor, Bootstrap, Workspace, GitHub-Project, Delivery, Proposal, Review, QA, CLI, Hub, Release, Requirements, Observability, Security.\n"
            "- Every feature, bug, or quality finding must include work_items. Each work item must be small, scoped, and suitable for a single coding agent.\n"
            "- Each work item must include review_role, qa_role, allowed_paths, tests, acceptance, worktree_hint, and should stay inside the same module as the finding. owner_role will be normalized to Coding-Agent by the runtime.\n"
            "- Bug work items must also include reproduction_steps, repo-relative test_case_files, and verification_steps. Do not leave bug reproduction implicit.\n"
            "- Test-gap work items should stay scoped to test files plus their target paths, and preserve blackbox/whitebox classification in test_gap_type.\n"
            "- Quality work items should prefer deleting dead files, consolidating duplicate code, extracting shared logic, or narrowing oversized modules. Do not propose cosmetic-only cleanup.\n"
            "- Coding work items must be issue-scoped only; no extra optimization outside the listed paths.\n"
            "- 所有面向用户的自然语言字段必须使用简体中文，包括 title、summary、rationale、acceptance、work_items.title、work_items.summary。\n"
            "- 保留 role id、路径、命令、状态枚举、版本号、URL、worktree_hint 为原样。\n"
            "- Also include repo-level ci_actions and notes.\n"
        ),
        expected_output="A structured JSON upgrade plan.",
        agent=issue_drafter,
        context=enabled_scan_tasks,
        output_json=UpgradePlan,
    )
    review_task = Task(
        name="review_delivery_plan",
        description=(
            (
                "Review the draft upgrade plan.\n"
                "Reject large or fuzzy work items. Ensure every coding work item has clear path scope, task-linked commit discipline, and explicit downstream review/QA roles.\n"
                "Reject any finding that spans multiple modules or uses an unstable module name.\n"
                "For quality items, reject vague refactors or cleanup that is not backed by concrete evidence from the repository context.\n"
                "For test-gap quality items, reject anything without a clear blackbox/whitebox classification, uncovered path, and suggested test file location.\n"
                "Keep all user-facing natural language fields in Simplified Chinese.\n"
            )
            + (
                f"Keep no more than {int(max_findings)} findings in the final output."
                if int(max_findings) > 0
                else "Keep every validated finding in the final output; do not invent filler findings."
            )
        ),
        expected_output="A validated structured JSON upgrade plan ready for issue/task recording.",
        agent=review_agent,
        context=[*enabled_scan_tasks, plan_task],
        output_json=UpgradePlan,
    )
    qa_task = Task(
        name="qa_acceptance_gate",
        description=(
            "Finalize the plan from a QA and release perspective.\n"
            "Make sure each work item has explicit tests and acceptance. Features and quality items must wait for user confirmation. Bugs can flow immediately.\n"
            "For test-gap quality items, require explicit blackbox/whitebox typing and target_paths/missing_paths so the issue can explain what remains untested.\n"
            "Preserve the single-module rule so downstream issue titles can follow [Type][Module] xxx.\n"
            "Keep all user-facing natural language fields in Simplified Chinese.\n"
            "No item should be closeable without review and QA acceptance."
        ),
        expected_output="A final structured JSON upgrade plan ready for runtime materialization.",
        agent=qa_agent,
        context=[*enabled_scan_tasks, plan_task, review_task],
        output_json=UpgradePlan,
    )

    crew = Crew(
        agents=enabled_agents,
        tasks=[*enabled_scan_tasks, plan_task, review_task, qa_task],
        process=Process.sequential,
        verbose=verbose,
    )
    with crewai_runtime.suppress_proxy_for_codex_oauth(model=str(getattr(llm, "model", "") or "")):
        out = crew.kickoff()
    current_version = str(repo_context.get("current_version") or "0.1.0").strip() or "0.1.0"
    plan = _coerce_plan(
        out,
        max_findings=max_findings,
        repo_root=Path(str(repo_context.get("repo_root") or ".")),
        current_version=current_version,
        project_id=project_id,
    )
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
    return _env_truthy("TEAMOS_RUNTIME_FILE_MIRROR", "1")


def _is_team_task_doc(doc: dict[str, Any]) -> bool:
    orchestration = doc.get("orchestration") or {}
    return isinstance(orchestration, dict) and _is_team_flow(orchestration.get("flow"))


def _iter_team_task_docs(*, project_id: str, target_id: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in improvement_store.list_delivery_tasks(project_id=str(project_id or ""), target_id=str(target_id or "")):
        if not isinstance(doc, dict) or not _is_team_task_doc(doc):
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
        if not isinstance(doc, dict) or not _is_team_task_doc(doc):
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
            if _is_team_task_doc(doc):
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
        if _is_team_task_doc(doc):
            try:
                improvement_store.upsert_delivery_task(doc)
            except Exception:
                pass
        return doc
    except Exception:
        return {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    payload = dict(payload or {})
    if _is_team_task_doc(payload or {}):
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
            str(finding.lane or "").strip().lower(),
            str(finding.module or "").strip().lower(),
            str(finding.title or "").strip().lower(),
            str(finding.test_gap_type or "").strip().lower(),
            ",".join(sorted([str(x).strip() for x in (finding.missing_paths or []) if str(x).strip()])),
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
            str(item.test_gap_type or "").strip().lower(),
            ",".join(sorted([str(x).strip() for x in (item.suggested_test_files or []) if str(x).strip()])),
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


def _team_source_label(team_id: str) -> str:
    return f"source:{_normalize_team_id(team_id)}"


def _proposal_issue_labels(doc: dict[str, Any]) -> list[str]:
    lane = str(doc.get("lane") or "feature").strip().lower() or "feature"
    module = _proposal_module(doc)
    team_id = _normalize_team_id(str(doc.get("team_id") or ((doc.get("team") or {}) if isinstance(doc.get("team"), dict) else {}).get("team_id") or ""))
    labels = [
        "teamos",
        _team_source_label(team_id),
        f"type:{lane if lane in ('feature', 'bug', 'process', 'quality') else 'feature'}",
        f"module:{_module_slug(module)}",
        "stage:proposal",
        _proposal_status_label(str(doc.get("status") or "")),
        _version_label(str(doc.get("version_bump") or "")),
    ]
    test_gap_type = str(doc.get("test_gap_type") or "").strip().lower()
    if lane == "quality" and test_gap_type in ("blackbox", "whitebox"):
        labels.append(f"test-gap:{test_gap_type}")
    return sorted({str(x).strip() for x in labels if str(x).strip()})


def _task_issue_stage_label(doc: dict[str, Any]) -> str:
    execution = _team_section(doc, key="team_execution")
    stage = str((execution if isinstance(execution, dict) else {}).get("stage") or "").strip().lower()
    status = str(doc.get("status") or "").strip().lower()
    if status in ("needs_clarification",):
        return "stage:needs-clarification"
    if stage in ("audit", "proof_bootstrap", "proof_verify", "coding", "review", "qa", "docs", "release", "blocked", "closed", "merge_conflict", "needs_clarification"):
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


def _coding_contract_labels(doc: dict[str, Any], *, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> list[str]:
    contract = _coding_contract(doc, finding=finding, work_item=work_item)
    approval = contract.get("approval") or {}
    proof = contract.get("proof") or {}
    documentation = contract.get("documentation") or {}
    return [
        f"approval:{str(approval.get('state') or 'pending').strip().lower() or 'pending'}",
        "proof:required" if bool(proof.get("required")) else "proof:not-required",
        "proof:bootstrap" if bool(proof.get("bootstrap_if_missing")) else "proof:no-bootstrap",
        f"proof-failure:{str(proof.get('failure_policy') or 'block').strip().lower() or 'block'}",
        "docs:required" if bool(documentation.get("required")) else "docs:not-required",
    ]


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
    team_id = _normalize_team_id(str(doc.get("team_id") or ((doc.get("team") or {}) if isinstance(doc.get("team"), dict) else {}).get("team_id") or ""))
    labels = [
        "teamos",
        _team_source_label(team_id),
        f"type:{lane if lane in ('feature', 'bug', 'process', 'quality') else 'bug'}",
        f"module:{_module_slug(module)}",
        _task_issue_stage_label(doc),
        _version_label(str(finding.version_bump or "")),
    ]
    milestone_doc = _team_section(doc, key="team_milestone")
    if not isinstance(milestone_doc, dict):
        milestone_doc = {}
    milestone_title = ""
    if lane in ("feature", "bug"):
        milestone_title = str(milestone_doc.get("title") or _milestone_title_for_target_version(str(finding.target_version or ""))).strip()
    if milestone_title:
        labels.append(f"milestone:{_module_slug(milestone_title)}")
    test_gap_type = str(work_item.test_gap_type or finding.test_gap_type or "").strip().lower()
    if lane == "quality" and test_gap_type in ("blackbox", "whitebox"):
        labels.append(f"test-gap:{test_gap_type}")
    labels.extend(_coding_contract_labels(doc, finding=finding, work_item=work_item))
    return sorted({str(x).strip() for x in labels if str(x).strip()})


def _task_issue_audit_lines(doc: dict[str, Any], *, finding: UpgradeFinding) -> list[str]:
    audit = _team_section(doc, key="team_audit")
    if not isinstance(audit, dict):
        audit = {}
    lane = str(audit.get("classification") or finding.lane or "").strip().lower() or str(finding.lane or "bug").strip().lower() or "bug"
    closure = str(audit.get("closure") or audit.get("status") or "pending").strip() or "pending"
    feedback = [str(x).strip() for x in (audit.get("feedback") or []) if str(x).strip()]
    reproduction_steps = [str(x).strip() for x in (audit.get("reproduction_steps") or []) if str(x).strip()]
    test_case_files = [str(x).strip() for x in (audit.get("test_case_files") or []) if str(x).strip()]
    reproduction_commands = [str(x).strip() for x in (audit.get("reproduction_commands") or []) if str(x).strip()]
    verification_steps = [str(x).strip() for x in (audit.get("verification_steps") or []) if str(x).strip()]
    verification_commands = [str(x).strip() for x in (audit.get("verification_commands") or []) if str(x).strip()]
    lines = [
        f"- 审计角色: {role_display_zh(str(audit.get('audit_role') or ROLE_ISSUE_AUDIT_AGENT))} ({str(audit.get('audit_role') or ROLE_ISSUE_AUDIT_AGENT)})",
        f"- 当前状态: {str(audit.get('status') or 'pending')}",
        f"- 问题分类: {_issue_type_token(lane)}",
        f"- 闭环性: {closure}",
        f"- 值得进入开发: {'是' if bool(audit.get('worth_doing', True)) else '否'}",
        f"- 需要文档同步: {'是' if bool(audit.get('docs_required', False)) else '否'}",
    ]
    if lane == "bug":
        lines.append(f"- 审计已实际复现: {'是' if bool(audit.get('reproduced_in_audit')) else '否'}")
    if str(audit.get("summary") or "").strip():
        lines.append(f"- 审计结论: {str(audit.get('summary') or '').strip()}")
    if lane == "bug":
        lines.extend([f"- 复现路径: {item}" for item in reproduction_steps] or ["- 复现路径: （未指定）"])
        lines.extend([f"- 测试 case 脚本: {item}" for item in test_case_files] or ["- 测试 case 脚本: （未指定）"])
        lines.extend([f"- 复现测试命令: {item}" for item in reproduction_commands] or ["- 复现测试命令: （未指定）"])
        lines.extend([f"- 修复后验证步骤: {item}" for item in verification_steps] or ["- 修复后验证步骤: （未指定）"])
        if verification_commands:
            lines.extend([f"- 修复后验证命令: {item}" for item in verification_commands])
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


def _task_issue_coding_contract_lines(doc: dict[str, Any], *, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> list[str]:
    contract = _coding_contract(doc, finding=finding, work_item=work_item)
    approval = contract.get("approval") or {}
    proof = contract.get("proof") or {}
    documentation = contract.get("documentation") or {}
    return [
        f"- 审批要求: {'需要' if bool(approval.get('required')) else '不需要'}",
        f"- 审批状态: {str(approval.get('state') or 'pending')}",
        f"- 证据要求: {'需要' if bool(proof.get('required')) else '不需要'}",
        f"- 证据缺失时自动补齐: {'是' if bool(proof.get('bootstrap_if_missing')) else '否'}",
        f"- 证据失败策略: {str(proof.get('failure_policy') or 'block')}",
        f"- 文档同步要求: {'需要' if bool(documentation.get('required')) else '不需要'}",
        f"- 编码角色: {role_display_zh(work_item.owner_role)} ({work_item.owner_role})",
        f"- 评审角色: {role_display_zh(work_item.review_role)} ({work_item.review_role})",
        f"- QA 角色: {role_display_zh(work_item.qa_role)} ({work_item.qa_role})",
    ]


def _task_issue_milestone_lines(doc: dict[str, Any], *, finding: UpgradeFinding) -> list[str]:
    milestone = doc.get("team_milestone") or {}
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
        if not isinstance(orchestration, dict) or not _is_team_flow(orchestration.get("flow")):
            return
        milestone = _team_section(doc, key="team_milestone")
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
    for doc in _iter_team_task_docs(project_id=project_id):
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
        return {"ok": False, "reason": "missing_team_finding"}
    project_id = str(doc.get("project_id") or "teamos").strip() or "teamos"
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    repo_locator = str(repo.get("locator") or "").strip()
    existing = doc.get("team_milestone") or {}
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
        extra_doc={**doc, "team_milestone": dict(milestone)},
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
        description = f"Team OS team workflow release milestone for {str(milestone.get('title') or '').strip()}."
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
                _team_source_label(str(doc.get("team_id") or ((doc.get("team") or {}) if isinstance(doc.get("team"), dict) else {}).get("team_id") or "")),
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
    doc["team_milestone"] = milestone
    return {"ok": True, "milestone": milestone}


def _task_issue_milestone_number(*, repo_locator: str, finding: UpgradeFinding, doc: Optional[dict[str, Any]] = None) -> Optional[int]:
    milestone = (doc or {}).get("team_milestone") if isinstance(doc, dict) else None
    if isinstance(milestone, dict):
        num = int(milestone.get("github_milestone_number") or 0)
        if num > 0:
            return num
    lane = str(finding.lane or "").strip().lower()
    milestone_title = _milestone_title_for_target_version(str(finding.target_version or ""))
    if lane not in ("feature", "bug") or not milestone_title or not repo_locator:
        return None
    description = f"Team OS team workflow release milestone for {milestone_title}."
    try:
        return ensure_milestone(repo_locator, title=milestone_title, description=description)
    except (GitHubAuthError, GitHubIssuesBusError):
        return None


def list_proposals(*, team_id: str = "", target_id: str = "", project_id: str = "", lane: str = "", status: str = "") -> list[dict[str, Any]]:
    return improvement_store.list_proposals(
        team_id=_normalize_team_id(team_id),
        target_id=str(target_id or "").strip(),
        project_id=str(project_id or "").strip(),
        lane=lane,
        status=status,
    )


def decide_proposal(
    *,
    team_id: str = "",
    proposal_id: str,
    action: str,
    title: str = "",
    summary: str = "",
    version_bump: str = "",
) -> dict[str, Any]:
    pid = str(proposal_id or "").strip()
    if not pid:
        raise TeamWorkflowError("proposal_id is required")
    act = str(action or "").strip().lower()
    if act not in ("approve", "reject", "hold"):
        raise TeamWorkflowError("action must be one of: approve, reject, hold")
    doc = improvement_store.get_proposal(pid)
    if not isinstance(doc, dict):
        raise TeamWorkflowError(f"proposal not found: {pid}")
    normalized_team_id = _normalize_team_id(team_id)
    if str(doc.get("team_id") or "").strip() not in {"", normalized_team_id}:
        raise TeamWorkflowError(f"proposal_team_mismatch: {pid}")
    now = _utc_now_iso()
    if title:
        doc["title"] = str(title).strip()
    if summary:
        doc["summary"] = str(summary).strip()
    if version_bump:
        vb = str(version_bump).strip().lower()
        if vb not in ("major", "minor", "patch", "none"):
            raise TeamWorkflowError("version_bump must be one of: major, minor, patch, none")
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
        raise TeamWorkflowError("proposal_id is required")
    doc = improvement_store.get_proposal(pid)
    if not isinstance(doc, dict):
        raise TeamWorkflowError(f"proposal not found: {pid}")
    now = _utc_now_iso()
    if title:
        doc["title"] = str(title).strip()
    if summary:
        doc["summary"] = str(summary).strip()
    if version_bump:
        vb = str(version_bump).strip().lower()
        if vb not in ("major", "minor", "patch", "none"):
            raise TeamWorkflowError("version_bump must be one of: major, minor, patch, none")
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
    test_gap_type = str(doc.get("test_gap_type") or "").strip().lower()
    if lane == "quality" and test_gap_type in ("blackbox", "whitebox"):
        lines.extend(
            [
                "",
                "## 测试缺口分析",
                "",
                f"- 测试缺口类型: {test_gap_type}",
            ]
        )
        lines.extend([f"- 目标路径: {x}" for x in (doc.get("target_paths") or [])] or ["- 目标路径: （未指定）"])
        lines.extend([f"- 未测路径: {x}" for x in (doc.get("missing_paths") or [])] or ["- 未测路径: （未指定）"])
        lines.extend([f"- 建议测试文件: {x}" for x in (doc.get("suggested_test_files") or [])] or ["- 建议测试文件: （未指定）"])
        lines.append(f"- 未覆盖原因: {_normalize_issue_text(str(doc.get('why_not_covered') or ''), empty_fallback='（未指定）')}")
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
    agent = crewai_agent_factory.build_crewai_agent(
        role_id=ROLE_ISSUE_DISCUSSION_AGENT,
        team_id=_normalize_team_id(str(proposal.get("team_id") or "")),
        llm=llm,
        verbose=verbose,
    )
    task = Task(
        name="reply_to_improvement_proposal_discussion",
        description=(
            "Read the proposal and the latest user comments.\n"
            "Return JSON matching ProposalDiscussionResponse.\n"
            "Rules:\n"
            "- If the user is only asking questions or suggesting changes, keep action=pending or hold.\n"
            "- Only set action=approve when the user explicitly confirms the proposal should proceed.\n"
            "- You may refine title, summary, version_bump, or module if the user feedback clearly changes the scope.\n"
            "- module must stay a single stable value such as Runtime, Team-Workflow, CI, Doctor, Bootstrap, Workspace, GitHub-Project, Delivery, Proposal, Review, QA, CLI, Hub, Release, Requirements, Observability, Security.\n"
            "- Keep the reply concise and directly answer the user's latest questions.\n"
            "- 所有 reply_body、title、summary 必须使用简体中文。\n\n"
            f"Payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        ),
        expected_output="A structured JSON discussion reply.",
        agent=agent,
        output_json=ProposalDiscussionResponse,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=verbose)
    with crewai_runtime.suppress_proxy_for_codex_oauth(model=str(getattr(llm, "model", "") or "")):
        out = crew.kickoff()
    if hasattr(out, "to_dict"):
        return ProposalDiscussionResponse.model_validate(out.to_dict())
    if hasattr(out, "json_dict"):
        return ProposalDiscussionResponse.model_validate(getattr(out, "json_dict"))
    text = str(out or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise TeamWorkflowError("CrewAI returned no structured discussion reply")
    return ProposalDiscussionResponse.model_validate(json.loads(match.group(0)))


def reconcile_feature_discussions(
    *,
    db=None,
    actor: str = "team_workflow_discussion_loop",
    verbose: bool = False,
    project_id: str = "",
    target_id: str = "",
    team_id: str = "",
) -> dict[str, Any]:
    from app.engines.crewai.workflow_runner import WorkflowRunContext, run_workflow

    stats = {"scanned": 0, "updated": 0, "replied": 0, "errors": 0, "skipped_disabled": 0, "skipped_runtime": 0}
    normalized_project_id = str(project_id or "teamos").strip() or "teamos"
    normalized_target_id = str(target_id or "").strip()
    workflows = [
        workflow
        for workflow in crewai_workflow_registry.list_workflows(team_id=_normalize_team_id(team_id), project_id=normalized_project_id)
        if workflow.phase == crewai_workflow_registry.PHASE_DISCUSSION and workflow.enabled
    ]
    for workflow in workflows:
        runtime_policy = crewai_workflow_registry.evaluate_workflow_runtime_policy(
            workflow=workflow,
            target_id=normalized_target_id,
            force=False,
        )
        _ = crewai_workflow_registry.update_workflow_runtime_state(normalized_target_id, workflow.workflow_id, runtime_policy)
        if not runtime_policy.allowed:
            stats["skipped_runtime"] += 1
            continue
        result = run_workflow(
            context=WorkflowRunContext(
                db=db,
                workflow=workflow,
                actor=actor,
                project_id=normalized_project_id,
                workstream_id="general",
                target_id=normalized_target_id,
                dry_run=False,
                force=False,
                extra={"verbose": bool(verbose)},
            )
        )
        claim_outputs = dict((((result.get("state") or {}).get("tasks") or {}).get("claim_discussion") or {}).get("outputs") or {})
        if claim_outputs.get("proposal"):
            stats["scanned"] += 1
        apply_outputs = dict((((result.get("state") or {}).get("tasks") or {}).get("apply_discussion") or {}).get("outputs") or {})
        if apply_outputs.get("updated"):
            stats["updated"] += 1
            stats["replied"] += 1
        if not bool(result.get("ok", True)):
            stats["errors"] += 1
    return stats


def _upsert_proposal(
    *,
    team_id: str = "",
    target_id: str,
    repo_root: Path,
    repo_locator: str,
    project_id: str,
    finding: UpgradeFinding,
    current_version: str,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    finding = _localize_finding_to_zh(finding)
    workflow = crewai_workflow_registry.workflow_for_lane_phase(
        finding.lane,
        crewai_workflow_registry.PHASE_FINDING,
        team_id=_normalize_team_id(team_id),
        project_id=project_id,
    )
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
        "team_id": _normalize_team_id(team_id),
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
        "test_gap_type": str(finding.test_gap_type or "").strip().lower(),
        "target_paths": [str(x).strip() for x in (finding.target_paths or []) if str(x).strip()],
        "missing_paths": [str(x).strip() for x in (finding.missing_paths or []) if str(x).strip()],
        "suggested_test_files": [str(x).strip() for x in (finding.suggested_test_files or []) if str(x).strip()],
        "why_not_covered": str(finding.why_not_covered or "").strip(),
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
        "team": {
            "team_id": _normalize_team_id(team_id),
            "lane": finding.lane,
            "phase": workflow.phase,
            "workflow_id": workflow.workflow_id,
        },
        "orchestration": {
            "engine": "crewai",
            "flow": _team_flow_id(team_id),
            "finding_lane": finding.lane,
            "workflow_id": workflow.workflow_id,
        },
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


def _task_issue_comment_reason(doc: dict[str, Any]) -> str:
    status = str(doc.get("status") or "").strip().lower()
    checkpoint = doc.get("checkpoint") or {}
    if not isinstance(checkpoint, dict):
        checkpoint = {}
    stage = str(checkpoint.get("stage") or "").strip().lower()
    execution = doc.get("team_execution") or {}
    if not isinstance(execution, dict):
        execution = {}
    feedback = [str(x).strip() for x in (execution.get("last_feedback") or []) if str(x).strip()]
    last_error = str(execution.get("last_error") or "").strip()
    title = str(doc.get("title") or doc.get("summary") or doc.get("id") or "task").strip()
    reason = f"Syncing Team OS issue metadata for `{title}`."
    details: list[str] = []
    if status:
        details.append(f"status={status}")
    if stage:
        details.append(f"stage={stage}")
    if feedback:
        details.append(f"feedback={feedback[0]}")
    elif last_error:
        details.append(f"error={last_error}")
    if details:
        reason += " Reason: " + "; ".join(details[:3])
    return reason


def _ensure_task_record(
    *,
    team_id: str = "",
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
        raise TeamWorkflowError(f"failed to materialize task record for finding={finding.title}")

    existing_execution = doc.get("team_execution") or {}
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
    existing_audit = doc.get("team_audit") or {}
    if not isinstance(existing_audit, dict):
        existing_audit = {}
    default_docs = _default_documentation_policy(finding=finding, work_item=work_item)
    existing_docs = doc.get("documentation_policy") or {}
    if not isinstance(existing_docs, dict):
        existing_docs = {}
    existing_milestone = doc.get("team_milestone") or {}
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
    coding_contract = _build_coding_contract(
        finding=finding,
        work_item=work_item,
        proposal_id=proposal_id,
        documentation_required=bool(documentation_policy["required"]),
        existing=doc.get("coding_contract") if isinstance(doc.get("coding_contract"), dict) else {},
    )
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
        ROLE_CODING_AGENT,
        work_item.owner_role,
        work_item.review_role,
        work_item.qa_role,
        str(documentation_policy.get("documentation_role") or ROLE_DOCUMENTATION_AGENT),
    ]
    doc["need_pm_decision"] = False
    normalized_team_id = _normalize_team_id(team_id)
    coding_workflow = crewai_workflow_registry.workflow_for_phase(
        crewai_workflow_registry.PHASE_CODING,
        team_id=normalized_team_id,
        project_id=panel_project_id,
    )
    doc["team_id"] = normalized_team_id
    doc["orchestration"] = {
        "engine": "crewai",
        "flow": f"team:{normalized_team_id}",
        "finding_kind": finding.kind,
        "finding_lane": finding.lane,
        "finding_fingerprint": _finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding),
        "work_item_key": work_item_key,
        "proposal_id": proposal_id,
    }
    doc["team"] = {
        "team_id": normalized_team_id,
        "lane": finding.lane,
        "phase": coding_workflow.phase,
        "workflow_id": coding_workflow.workflow_id,
    }
    doc["workflows"] = [normalized_team_id]
    team_workflow_doc = {
        "kind": finding.kind,
        "lane": finding.lane,
        "module": finding.module,
        "summary": finding.summary,
        "rationale": finding.rationale,
        "impact": finding.impact,
        "files": finding.files,
        "tests": finding.tests,
        "acceptance": finding.acceptance,
        "test_gap_type": str(finding.test_gap_type or "").strip().lower(),
        "target_paths": [str(x).strip() for x in (finding.target_paths or []) if str(x).strip()],
        "missing_paths": [str(x).strip() for x in (finding.missing_paths or []) if str(x).strip()],
        "suggested_test_files": [str(x).strip() for x in (finding.suggested_test_files or []) if str(x).strip()],
        "why_not_covered": str(finding.why_not_covered or "").strip(),
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
    doc["team_workflow"] = dict(team_workflow_doc)
    team_execution = {
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
    doc["team_execution"] = dict(team_execution)
    doc["team_audit"] = dict(audit_doc)
    doc["team_milestone"] = dict(milestone_doc)
    doc["documentation_policy"] = documentation_policy
    doc["coding_contract"] = coding_contract
    doc["execution_policy"] = {
        "issue_only_scope": True,
        "allowed_paths": list(work_item.allowed_paths or []),
        "worktree_hint": normalized_worktree_hint,
        "commit_message_template": f"{task_id}: {work_item.title}",
        "issue_id_required": True,
        "no_extra_optimization": True,
        "owner_role": ROLE_CODING_AGENT,
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
    issue_marker = str(marker or "").strip() or f"<!-- teamos:team_workflow:{fingerprint} -->"
    lines = [
        issue_marker,
        "# 仓库改进任务",
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
    ])
    if str(finding.lane or "").strip().lower() == "bug":
        lines.extend(
            [
                "## Bug 复现与修复验证",
                "",
            ]
        )
        lines.extend([f"- 复现路径: {x}" for x in (work_item.reproduction_steps or [])] or ["- 复现路径: （未指定）"])
        lines.extend([f"- 测试 case 脚本: {x}" for x in (work_item.test_case_files or [])] or ["- 测试 case 脚本: （未指定）"])
        lines.extend([f"- 修复后验证步骤: {x}" for x in (work_item.verification_steps or work_item.acceptance or [])] or ["- 修复后验证步骤: （未指定）"])
        lines.extend([""])
    if str(finding.lane or "").strip().lower() == "quality" and str(work_item.test_gap_type or finding.test_gap_type or "").strip().lower() in ("blackbox", "whitebox"):
        lines.extend(
            [
                "## 测试缺口分析",
                "",
                f"- 测试缺口类型: {str(work_item.test_gap_type or finding.test_gap_type or '').strip().lower()}",
            ]
        )
        lines.extend([f"- 目标路径: {x}" for x in (work_item.target_paths or finding.target_paths or [])] or ["- 目标路径: （未指定）"])
        lines.extend([f"- 未测路径: {x}" for x in (work_item.missing_paths or finding.missing_paths or [])] or ["- 未测路径: （未指定）"])
        lines.extend([f"- 建议测试文件: {x}" for x in (work_item.suggested_test_files or finding.suggested_test_files or [])] or ["- 建议测试文件: （未指定）"])
        lines.append(f"- 未覆盖原因: {_normalize_issue_text(str(work_item.why_not_covered or finding.why_not_covered or ''), empty_fallback='（未指定）')}")
        lines.extend([""])
    lines.extend(
        [
            "## 范围内",
            "",
        ]
    )
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
            "## 编码契约",
            "",
        ]
    )
    lines.extend(_task_issue_coding_contract_lines(doc or {}, finding=finding, work_item=work_item))
    lines.extend(
        [
            "",
            "## 执行约束",
            "",
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
        return {"ok": False, "reason": "missing_team_finding"}
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
            create_issue_comment(repo_locator, issue_number, body=_task_issue_comment_reason(doc))
            issue = update_issue(repo_locator, issue_number, title=title, body=body, labels=labels, state=issue_state, milestone=milestone)
        else:
            created = _ensure_issue_record(repo_locator=repo_locator, repo_root=repo_root, finding=finding, work_item=work_item)
            if created.error or not created.url:
                return {"ok": False, "reason": created.error or "issue_create_failed", "title": created.title}
            issue_number = _issue_number_from_url(created.url)
            create_issue_comment(repo_locator, issue_number, body=_task_issue_comment_reason(doc))
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
    su = doc.get("team_workflow") or {}
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
            reproduction_steps=[str(x).strip() for x in (work_item_raw.get("reproduction_steps") or []) if str(x).strip()],
            test_case_files=[str(x).strip() for x in (work_item_raw.get("test_case_files") or []) if str(x).strip()],
            verification_steps=[str(x).strip() for x in (work_item_raw.get("verification_steps") or work_item_raw.get("acceptance") or su.get("acceptance") or []) if str(x).strip()],
            test_gap_type=str(work_item_raw.get("test_gap_type") or su.get("test_gap_type") or "").strip().lower(),
            target_paths=[str(x).strip() for x in (work_item_raw.get("target_paths") or su.get("target_paths") or []) if str(x).strip()],
            missing_paths=[str(x).strip() for x in (work_item_raw.get("missing_paths") or su.get("missing_paths") or []) if str(x).strip()],
            suggested_test_files=[str(x).strip() for x in (work_item_raw.get("suggested_test_files") or su.get("suggested_test_files") or []) if str(x).strip()],
            why_not_covered=str(work_item_raw.get("why_not_covered") or su.get("why_not_covered") or "").strip(),
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
            test_gap_type=str(su.get("test_gap_type") or work_item.test_gap_type or "").strip().lower(),
            target_paths=[str(x).strip() for x in (su.get("target_paths") or work_item.target_paths or []) if str(x).strip()],
            missing_paths=[str(x).strip() for x in (su.get("missing_paths") or work_item.missing_paths or []) if str(x).strip()],
            suggested_test_files=[str(x).strip() for x in (su.get("suggested_test_files") or work_item.suggested_test_files or []) if str(x).strip()],
            why_not_covered=str(su.get("why_not_covered") or work_item.why_not_covered or "").strip(),
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


def sync_existing_team_workflow_github_content_to_zh(*, project_id: str = "teamos") -> dict[str, Any]:
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
    for localized_doc in _iter_team_task_docs(project_id=project_id):
        try:
            if not isinstance(localized_doc, dict) or not _is_team_task_doc(localized_doc):
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
                su = localized_doc.get("team_workflow") or {}
                if isinstance(su, dict):
                    su = dict(su)
                    su["module"] = finding.module
                    wi = su.get("work_item") or {}
                    if isinstance(wi, dict):
                        wi = dict(wi)
                        wi["module"] = work_item.module
                        su["work_item"] = wi
                    localized_doc["team_workflow"] = su
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


def _enabled_planning_workflow_ids(*, project_id: str) -> set[str]:
    enabled: set[str] = set()
    for workflow_id in (
        crewai_role_registry.WORKFLOW_FEATURE_FINDING,
        crewai_role_registry.WORKFLOW_BUG_FINDING,
        crewai_role_registry.WORKFLOW_QUALITY_FINDING,
        crewai_role_registry.WORKFLOW_PROCESS_FINDING,
    ):
        try:
            spec = crewai_workflow_registry.workflow_spec(workflow_id, project_id=project_id)
        except Exception:
            continue
        if bool(spec.enabled):
            enabled.add(spec.workflow_id)
    return enabled


def _use_bug_only_fast_path(*, project_id: str, enabled_workflows: set[str] | None = None) -> bool:
    workflows = enabled_workflows if enabled_workflows is not None else _enabled_planning_workflow_ids(project_id=project_id)
    return workflows == {crewai_role_registry.WORKFLOW_BUG_FINDING}


def _planning_role_ids(*, project_id: str) -> set[str]:
    enabled_workflows = _enabled_planning_workflow_ids(project_id=project_id)
    if _use_bug_only_fast_path(project_id=project_id, enabled_workflows=enabled_workflows):
        return {ROLE_TEST_MANAGER}
    roles = {
        ROLE_ISSUE_DRAFTER,
        ROLE_PLAN_REVIEW_AGENT,
        ROLE_PLAN_QA_AGENT,
    }
    if crewai_role_registry.WORKFLOW_FEATURE_FINDING in enabled_workflows:
        roles.update({ROLE_PRODUCT_MANAGER, ROLE_MILESTONE_MANAGER})
    if crewai_role_registry.WORKFLOW_BUG_FINDING in enabled_workflows:
        roles.add(ROLE_TEST_MANAGER)
    if crewai_role_registry.WORKFLOW_QUALITY_FINDING in enabled_workflows:
        roles.update({ROLE_TEST_CASE_GAP_AGENT, ROLE_CODE_QUALITY_ANALYST})
    if crewai_role_registry.WORKFLOW_PROCESS_FINDING in enabled_workflows:
        roles.add(ROLE_PROCESS_OPTIMIZATION_ANALYST)
    return roles


def _register_agents(*, db, project_id: str, workstream_id: str, task_id: str, role_filter: set[str] | None = None) -> dict[str, str]:
    blueprint = crewai_role_registry.planning_team_blueprint()
    if role_filter:
        blueprint = crewai_role_registry.TeamBlueprint(
            team_id=blueprint.team_id,
            members=tuple(member for member in blueprint.members if member.role_id in role_filter),
        )
    return crewai_role_registry.register_team_blueprint(
        db=db,
        blueprint=blueprint,
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
    team_id: str = "",
    target_id: str,
    repo_root: Path,
    repo_locator: str,
    project_id: str,
    finding: UpgradeFinding,
    work_item: UpgradeWorkItem,
    proposal_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    workflow = crewai_workflow_registry.workflow_for_lane_phase(
        finding.lane,
        crewai_workflow_registry.PHASE_CODING,
        team_id=_normalize_team_id(team_id),
        project_id=project_id,
    )
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
        team_id=_normalize_team_id(team_id),
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


def run_team_workflow(*, db, spec: Any, actor: str, run_id: str, crewai_info: dict[str, Any]) -> dict[str, Any]:
    from app.engines.crewai.workflow_runner import WorkflowRunContext, run_workflow

    team_id = _normalize_team_id(crew_tools.native_team_id(str(getattr(spec, "flow", "") or "")))
    project_id = _safe_project_id(str(getattr(spec, "project_id", "teamos") or "teamos"))
    workstream_id = str(getattr(spec, "workstream_id", "") or "general").strip() or "general"
    target = _resolve_target(
        target_id=str(getattr(spec, "target_id", "") or ""),
        repo_path=str(getattr(spec, "repo_path", "") or ""),
        repo_url=str(getattr(spec, "repo_url", "") or ""),
        repo_locator=str(getattr(spec, "repo_locator", "") or ""),
        project_id=project_id,
    )
    target_id = str(target.get("target_id") or "").strip() or "teamos"
    repo_root = Path(str(target.get("repo_root") or team_os_root())).expanduser().resolve()
    repo_locator = str(target.get("repo_locator") or "").strip()
    trigger = str(getattr(spec, "trigger", "") or "manual").strip() or "manual"
    dry_run = bool(getattr(spec, "dry_run", False))
    force = bool(getattr(spec, "force", False))

    workflows = [
        workflow
        for workflow in crewai_workflow_registry.list_workflows(team_id=team_id, project_id=project_id)
        if workflow.phase == crewai_workflow_registry.PHASE_FINDING and workflow.enabled
    ]
    if not workflows:
        payload = {
            "ok": True,
            "skipped": True,
            "reason": "no_enabled_workflows",
            "team_id": team_id,
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
            {
                "ts": _utc_now_iso(),
                "target_id": target_id,
                "repo_root": str(repo_root),
                "repo_locator": repo_locator,
                "status": "SKIPPED",
                "reason": "no_enabled_workflows",
            },
        )
        db.add_event(
            event_type="TEAM_WORKFLOW_SKIPPED",
            actor=actor,
            project_id=project_id,
            workstream_id=workstream_id,
            payload=payload,
        )
        return payload

    db.add_event(
        event_type="TEAM_WORKFLOW_STARTED",
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

    records: list[dict[str, Any]] = []
    pending_proposals: list[dict[str, Any]] = []
    workflow_results: list[dict[str, Any]] = []
    summaries: list[str] = []
    ci_actions: list[str] = []
    notes: list[str] = []
    panel_sync: dict[str, Any] = {}
    current_version = "0.1.0"
    planned_version = "0.1.0"
    bug_finding_count = 0
    repo_context_for_bug_state: dict[str, Any] = {}
    bug_scan_policy: dict[str, Any] = {}

    for workflow in workflows:
        runtime_policy = crewai_workflow_registry.evaluate_workflow_runtime_policy(
            workflow=workflow,
            target_id=target_id,
            force=force,
        )
        _ = crewai_workflow_registry.update_workflow_runtime_state(target_id, workflow.workflow_id, runtime_policy)
        if not runtime_policy.allowed:
            workflow_results.append(
                {
                    "workflow_id": workflow.workflow_id,
                    "ok": True,
                    "skipped": True,
                    "reason": runtime_policy.reason,
                }
            )
            continue

        result = run_workflow(
            context=WorkflowRunContext(
                db=db,
                workflow=workflow,
                actor=actor,
                project_id=project_id,
                workstream_id=workstream_id,
                target_id=target_id,
                dry_run=dry_run,
                force=force,
                run_id=run_id,
                crewai_info=crewai_info,
                extra={
                    "repo_path": str(getattr(spec, "repo_path", "") or ""),
                    "repo_url": str(getattr(spec, "repo_url", "") or ""),
                    "repo_locator": str(getattr(spec, "repo_locator", "") or ""),
                    "team_id": team_id,
                },
            )
        )
        workflow_results.append(result)
        materialized = dict((((result.get("state") or {}).get("tasks") or {}).get("materialize_plan") or {}).get("outputs") or {})
        if not materialized:
            continue
        summaries.append(str(materialized.get("summary") or "").strip())
        records.extend(list(materialized.get("records") or []))
        pending_proposals.extend(list(materialized.get("pending_proposals") or []))
        panel_sync = dict(materialized.get("panel_sync") or panel_sync)
        current_version = str(materialized.get("current_version") or current_version).strip() or current_version
        planned_version = str(materialized.get("planned_version") or planned_version).strip() or planned_version
        plan_doc = dict(materialized.get("plan") or {}) if isinstance(materialized.get("plan"), dict) else {}
        ci_actions.extend([str(item).strip() for item in list(plan_doc.get("ci_actions") or []) if str(item).strip()])
        notes.extend([str(item).strip() for item in list(plan_doc.get("notes") or []) if str(item).strip()])
        if workflow.lane == "bug":
            bug_finding_count = len(list((plan_doc.get("findings") or [])))
            repo_context_for_bug_state = dict((((result.get("state") or {}).get("tasks") or {}).get("prepare_context") or {}).get("outputs") or {}).get("repo_context") or {}
            bug_scan_policy = dict(materialized.get("bug_scan_policy") or {})

    if repo_context_for_bug_state:
        bug_lane_state = _update_bug_lane_state(
            db=db,
            actor=actor,
            target_id=target_id,
            project_id=project_id,
            workstream_id=workstream_id,
            repo_context=repo_context_for_bug_state,
            bug_finding_count=bug_finding_count,
            policy=bug_scan_policy,
        )
    else:
        bug_lane_state = {}

    report = {
        "ts": _utc_now_iso(),
        "run_id": run_id,
        "target_id": target_id,
        "actor": actor,
        "trigger": trigger,
        "target": target,
        "repo_root": str(repo_root),
        "repo_locator": repo_locator,
        "project_id": project_id,
        "workflow_results": workflow_results,
        "records": records,
        "pending_proposals": pending_proposals,
        "panel_sync": panel_sync,
        "crewai": crewai_info,
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
            "bug_findings": bug_finding_count,
            "bug_lane_status": str((bug_lane_state or {}).get("status") or "active"),
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
            "bug_findings": bug_finding_count,
            "bug_lane_status": str((bug_lane_state or {}).get("status") or "active"),
            "pending_proposals": len(pending_proposals),
        },
    )
    db.add_event(
        event_type="TEAM_WORKFLOW_FINISHED",
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
            "bug_findings": bug_finding_count,
            "bug_lane_status": str((bug_lane_state or {}).get("status") or "active"),
            "pending_proposals": len(pending_proposals),
            "panel_sync": panel_sync,
            "report_id": run_id,
        },
    )
    summary = "\n".join([item for item in summaries if item]).strip()
    if not summary:
        summary = "Repo improvement workflow run completed."
    return {
        "ok": True,
        "run_id": run_id,
        "target_id": target_id,
        "repo_root": str(repo_root),
        "repo_locator": repo_locator,
        "project_id": project_id,
        "summary": summary,
        "ci_actions": ci_actions,
        "notes": notes,
        "current_version": current_version,
        "planned_version": planned_version,
        "bug_findings": bug_finding_count,
        "bug_lane_status": str((bug_lane_state or {}).get("status") or "active"),
        "records": records,
        "pending_proposals": pending_proposals,
        "panel_sync": panel_sync,
        "report_id": run_id,
        "crewai": crewai_info,
        "dry_run": dry_run,
        "workflow_results": workflow_results,
        "write_delegate": {
            "write_mode": "crewai_team_workflow",
            "writer": "workflow_runner",
            "truth_sources": ["task_ledger", "github_issues", "github_projects"],
            "target_repo": repo_locator or str(repo_root),
        },
    }
