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

from . import crew_tools
from . import codex_llm
from . import crewai_runtime
from .github_issues_bus import GitHubIssuesBusError, ensure_issue, list_issue_comments, update_issue, upsert_comment_with_marker
from .github_projects_client import GitHubAPIError, GitHubAuthError
from .panel_github_sync import GitHubProjectsPanelSync, PanelSyncError
from .panel_mapping import PanelMappingError, get_project_cfg, load_mapping
from .state_store import ledger_tasks_dir, runtime_state_root, team_os_root
from . import workspace_store


class SelfUpgradeError(RuntimeError):
    pass


class UpgradeWorkItem(BaseModel):
    title: str
    summary: str = ""
    owner_role: str = "Coding-Agent"
    review_role: str = "Review-Agent"
    qa_role: str = "QA-Agent"
    workstream_id: str = "general"
    allowed_paths: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    worktree_hint: str = ""


class UpgradeFinding(BaseModel):
    kind: str
    lane: str = "bug"
    title: str
    summary: str
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


class _IssueRecord(BaseModel):
    title: str
    url: str = ""
    error: str = ""


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


def _state_path() -> Path:
    return _runtime_root() / "state" / "self_upgrade_state.json"


def _proposals_path() -> Path:
    return _runtime_root() / "state" / "self_upgrade_proposals.json"


def _reports_dir() -> Path:
    return _runtime_root() / "state" / "self_upgrade" / "reports"


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


def _read_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_state(payload: dict[str, Any]) -> None:
    _write_json(_state_path(), payload)


def _read_proposals_state() -> dict[str, Any]:
    p = _proposals_path()
    if not p.exists():
        return {"items": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"items": {}}
    if not isinstance(raw, dict):
        return {"items": {}}
    items = raw.get("items")
    if not isinstance(items, dict):
        raw["items"] = {}
    return raw


def _write_proposals_state(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("items"), dict):
        payload["items"] = {}
    _write_json(_proposals_path(), payload)


def _read_run_history(limit: int = 12) -> list[dict[str, Any]]:
    state = _read_state()
    rows = state.get("history")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows[-max(1, int(limit)) :]:
        if isinstance(row, dict):
            out.append(row)
    return out


def _append_run_history(entry: dict[str, Any], *, keep: int = 30) -> None:
    state = _read_state()
    history = state.get("history")
    rows = list(history) if isinstance(history, list) else []
    rows.append(entry)
    state["history"] = rows[-max(1, int(keep)) :]
    _write_state(state)


def _merge_state_last_run(last_run: dict[str, Any], *, backoff_until: str = "") -> None:
    state = _read_state()
    state["last_run"] = last_run
    if backoff_until:
        state["backoff_until"] = backoff_until
    else:
        state.pop("backoff_until", None)
    _write_state(state)


def _should_skip(*, repo_root: Path, force: bool) -> tuple[bool, str]:
    if force:
        return False, ""
    state = _read_state()
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


def _target_repo_path(*, repo_path: str, project_id: str) -> Path:
    raw = str(repo_path or "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
        if not p.exists():
            raise SelfUpgradeError(f"target repo path does not exist: {p}")
        return p
    pid = str(project_id or "").strip() or "teamos"
    if pid != "teamos":
        p = workspace_store.project_repo_dir(pid)
        if p.exists() and any(p.iterdir()):
            return p.resolve()
    return team_os_root()


def collect_repo_context(*, repo_root: Path, explicit_repo_locator: str = "") -> dict[str, Any]:
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
        "recent_execution_metrics": _recent_execution_metrics(limit=8),
    }


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
    ln = str(lane or "bug").strip().lower()
    if ln == "feature":
        return "minor"
    if ln == "bug":
        return "patch"
    return "none"


def _lane_default_cooldown_hours(lane: str, *, requires_user_confirmation: bool) -> int:
    ln = str(lane or "bug").strip().lower()
    if ln == "feature":
        return int(os.getenv("TEAMOS_SELF_UPGRADE_FEATURE_COOLDOWN_HOURS", "1") or "1")
    if ln == "process":
        return int(os.getenv("TEAMOS_SELF_UPGRADE_PROCESS_COOLDOWN_HOURS", "24") or "24")
    return 0


def _lane_default_baseline_action(lane: str, version_bump: str) -> str:
    ln = str(lane or "bug").strip().lower()
    vb = str(version_bump or "").strip().lower()
    if ln == "feature":
        return "new_baseline" if vb in ("major", "minor") else "feature_followup"
    if ln == "bug":
        return "patch_release"
    return "process_improvement"


def _coding_owner_role(lane: str) -> str:
    ln = str(lane or "bug").strip().lower()
    if ln == "feature":
        return "Feature-Coding-Agent"
    if ln == "bug":
        return "Bugfix-Coding-Agent"
    return "Process-Optimization-Agent"


def _worktree_hint(*, repo_root: Path, lane: str, title: str) -> str:
    return _normalize_worktree_hint(repo_root=repo_root, lane=lane, title=title)


def _default_work_items(*, repo_root: Path, finding: UpgradeFinding) -> list[UpgradeWorkItem]:
    return [
        UpgradeWorkItem(
            title=str(finding.title or "").strip(),
            summary=str(finding.summary or "").strip(),
            owner_role=_coding_owner_role(finding.lane),
            review_role="Review-Agent",
            qa_role="QA-Agent",
            workstream_id=finding.workstream_id or "general",
            allowed_paths=list(finding.files or []),
            tests=list(finding.tests or []),
            acceptance=list(finding.acceptance or []),
            worktree_hint=_worktree_hint(repo_root=repo_root, lane=finding.lane, title=finding.title),
        )
    ]


def _recent_execution_metrics(limit: int = 8) -> list[dict[str, Any]]:
    return _read_run_history(limit=limit)


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
        elif raw_kind in ("BUG",):
            kind = "BUG"
        else:
            kind = "PROCESS"
        lane = str(getattr(finding, "lane", "") or "").strip().lower()
        if lane not in ("feature", "bug", "process"):
            lane = {"FEATURE": "feature", "BUG": "bug", "PROCESS": "process"}.get(kind, "bug")
        impact = str(finding.impact or "MED").strip().upper()
        if impact not in ("LOW", "MED", "HIGH"):
            impact = "MED"
        workstream_id = _slug(finding.workstream_id, default="general")
        version_bump = str(getattr(finding, "version_bump", "") or "").strip().lower()
        if version_bump not in ("major", "minor", "patch", "none"):
            version_bump = _lane_default_version_bump(lane)
        requires_user_confirmation = bool(getattr(finding, "requires_user_confirmation", False) or lane == "feature")
        cooldown_hours = int(getattr(finding, "cooldown_hours", 0) or _lane_default_cooldown_hours(lane, requires_user_confirmation=requires_user_confirmation))
        target_version = str(getattr(finding, "target_version", "") or "").strip() or _bump_version(current_version, version_bump)
        if version_bump == "none":
            target_version = current_version
        work_items: list[UpgradeWorkItem] = []
        for item in list(getattr(finding, "work_items", []) or [])[:6]:
            title = str(getattr(item, "title", "") or "").strip() or str(finding.title or "").strip() or "Untitled work item"
            work_items.append(
                UpgradeWorkItem(
                    title=title,
                    summary=str(getattr(item, "summary", "") or "").strip() or str(finding.summary or "").strip(),
                    owner_role=str(getattr(item, "owner_role", "") or "").strip() or _coding_owner_role(lane),
                    review_role=str(getattr(item, "review_role", "") or "").strip() or "Review-Agent",
                    qa_role=str(getattr(item, "qa_role", "") or "").strip() or "QA-Agent",
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
                )
            )
        finding_obj = UpgradeFinding(
            kind=kind,
            lane=lane,
            title=str(finding.title or "").strip() or "Untitled finding",
            summary=str(finding.summary or "").strip(),
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
        if not finding_obj.work_items and finding_obj.lane in ("feature", "bug"):
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
    from crewai import Agent, Crew, Process, Task

    repo_blob = json.dumps(repo_context, ensure_ascii=False, indent=2)
    llm = _crewai_llm()
    product_manager = Agent(
        role="Product Manager",
        goal="Identify worthwhile feature improvements and product-level optimizations for the target repository.",
        backstory="You think like a product manager. You prioritize user-visible value, versioning impact, and whether a change belongs in a new baseline.",
        llm=llm,
        allow_delegation=False,
        verbose=verbose,
    )
    test_manager = Agent(
        role="Test Manager",
        goal="Identify bugs, regressions, and missing black-box or white-box tests from the repository context.",
        backstory="You reason like a QA/test lead and focus on reproducible defects, weak test coverage, and operational risk.",
        llm=llm,
        allow_delegation=False,
        verbose=verbose,
    )
    issue_drafter = Agent(
        role="Issue Drafter",
        goal="Break features and bug fixes into small, execution-scoped engineering work items suitable for GitHub Projects and downstream coding agents.",
        backstory="You think like a delivery lead. You keep issues small, explicit, and scoped to one piece of work, with clear owner roles and worktree hints.",
        llm=llm,
        allow_delegation=False,
        verbose=verbose,
    )
    review_agent = Agent(
        role="Review Agent",
        goal="Enforce code review constraints so coding agents only touch issue-scoped files and commit history remains task-linked.",
        backstory="You act like an engineering reviewer protecting scope discipline, commit hygiene, and release boundaries.",
        llm=llm,
        allow_delegation=False,
        verbose=verbose,
    )
    qa_agent = Agent(
        role="QA Agent",
        goal="Ensure each work item has explicit verification, QA handoff, and close criteria before it can be considered done.",
        backstory="You are the final delivery gate. No item closes without review and QA evidence.",
        llm=llm,
        allow_delegation=False,
        verbose=verbose,
    )
    process_analyst = Agent(
        role="Process Optimization Analyst",
        goal="Use recent execution telemetry to identify improvements in the self-upgrade process itself.",
        backstory="You optimize the team workflow by looking at timings, failures, repeated blockers, and wasted motion.",
        llm=llm,
        allow_delegation=False,
        verbose=verbose,
    )

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
    plan_task = Task(
        name="draft_execution_backlog",
        description=(
            "Transform the feature scan, bug scan, and process scan into an actionable upgrade backlog.\n"
            "Output JSON matching UpgradePlan.\n"
            "Rules:\n"
            "- Features use lane=feature, kind=FEATURE, require user confirmation, and use version_bump=major or minor.\n"
            "- Bugs use lane=bug, kind=BUG, no user confirmation, and use version_bump=patch.\n"
            "- Process improvements use lane=process, kind=PROCESS, cooldown_hours=24, and version_bump=none.\n"
            "- Every feature or bug finding must include work_items. Each work item must be small, scoped, and suitable for a single coding agent.\n"
            "- Each work item must include owner_role, review_role, qa_role, allowed_paths, tests, acceptance, and worktree_hint.\n"
            "- Coding work items must be issue-scoped only; no extra optimization outside the listed paths.\n"
            "- Also include repo-level ci_actions and notes.\n"
        ),
        expected_output="A structured JSON upgrade plan.",
        agent=issue_drafter,
        context=[feature_task, bug_task, process_task],
        output_json=UpgradePlan,
    )
    review_task = Task(
        name="review_delivery_plan",
        description=(
            "Review the draft upgrade plan.\n"
            "Reject large or fuzzy work items. Ensure every coding work item has clear path scope, task-linked commit discipline, and explicit downstream review/QA roles.\n"
            f"Keep no more than {int(max_findings)} findings in the final output."
        ),
        expected_output="A validated structured JSON upgrade plan ready for issue/task recording.",
        agent=review_agent,
        context=[feature_task, bug_task, process_task, plan_task],
        output_json=UpgradePlan,
    )
    qa_task = Task(
        name="qa_acceptance_gate",
        description=(
            "Finalize the plan from a QA and release perspective.\n"
            "Make sure each work item has explicit tests and acceptance. Features must wait for user confirmation. Bugs can flow immediately.\n"
            "No item should be closeable without review and QA acceptance."
        ),
        expected_output="A final structured JSON upgrade plan ready for runtime materialization.",
        agent=qa_agent,
        context=[feature_task, bug_task, process_task, plan_task, review_task],
        output_json=UpgradePlan,
    )

    crew = Crew(
        agents=[product_manager, test_manager, issue_drafter, review_agent, qa_agent, process_analyst],
        tasks=[feature_task, bug_task, process_task, plan_task, review_task, qa_task],
        process=Process.sequential,
        verbose=verbose,
    )
    out = crew.kickoff()
    current_version = str(repo_context.get("current_version") or "0.1.0").strip() or "0.1.0"
    return _coerce_plan(out, max_findings=max_findings, repo_root=Path(str(repo_context.get("repo_root") or ".")), current_version=current_version), {
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


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _task_title_for_finding(finding: UpgradeFinding) -> str:
    kind = {"BUG": "BUG", "OPTIMIZATION": "OPT", "CI": "CI"}.get(str(finding.kind or "").upper(), "OPT")
    return f"[SELF-UPGRADE][{kind}] {finding.title}".strip()


def _issue_title_for_finding(repo_name: str, finding: UpgradeFinding) -> str:
    kind = {"BUG": "BUG", "OPTIMIZATION": "OPT", "CI": "CI"}.get(str(finding.kind or "").upper(), "OPT")
    return f"[Self-Upgrade][{kind}][{repo_name}] {finding.title}".strip()


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


def _task_title_for_work_item(finding: UpgradeFinding, item: UpgradeWorkItem) -> str:
    lane = {"feature": "FEATURE", "bug": "BUG", "process": "PROCESS"}.get(str(finding.lane or "").lower(), "WORK")
    owner = str(item.owner_role or "Coding-Agent").replace(" ", "-")
    return f"[SELF-UPGRADE][{lane}][{owner}] {item.title}".strip()


def _issue_title_for_work_item(repo_name: str, finding: UpgradeFinding, item: UpgradeWorkItem) -> str:
    lane = {"feature": "FEATURE", "bug": "BUG", "process": "PROCESS"}.get(str(finding.lane or "").lower(), "WORK")
    owner = str(item.owner_role or "Coding-Agent").replace(" ", "-")
    return f"[Self-Upgrade][{lane}][{owner}][{repo_name}] {item.title}".strip()


def list_proposals(*, lane: str = "", status: str = "") -> list[dict[str, Any]]:
    state = _read_proposals_state()
    items = state.get("items") if isinstance(state.get("items"), dict) else {}
    out: list[dict[str, Any]] = []
    lane_filter = str(lane or "").strip().lower()
    status_filter = str(status or "").strip().upper()
    for proposal_id, raw in sorted(items.items()):
        if not isinstance(raw, dict):
            continue
        doc = {"proposal_id": proposal_id, **raw}
        if lane_filter and str(doc.get("lane") or "").strip().lower() != lane_filter:
            continue
        if status_filter and str(doc.get("status") or "").strip().upper() != status_filter:
            continue
        out.append(doc)
    return sorted(out, key=lambda x: (str(x.get("updated_at") or ""), str(x.get("proposal_id") or "")), reverse=True)


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
    state = _read_proposals_state()
    items = state.get("items") if isinstance(state.get("items"), dict) else {}
    doc = items.get(pid)
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
    items[pid] = doc
    state["items"] = items
    _write_proposals_state(state)
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
    state_doc = _read_proposals_state()
    items = state_doc.get("items") if isinstance(state_doc.get("items"), dict) else {}
    doc = items.get(pid)
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
    doc["updated_at"] = now
    items[pid] = doc
    state_doc["items"] = items
    _write_proposals_state(state_doc)
    return {"proposal_id": pid, **doc}


def _proposal_issue_title(doc: dict[str, Any]) -> str:
    pid = str(doc.get("proposal_id") or "").strip()
    title = str(doc.get("title") or "Untitled feature proposal").strip()
    repo_root = Path(str(doc.get("repo_root") or "."))
    return f"[Feature Proposal][{repo_root.name}][{pid}] {title}".strip()


def _proposal_issue_labels(doc: dict[str, Any]) -> list[str]:
    status = str(doc.get("status") or "").strip().lower() or "pending_confirmation"
    return ["teamos", "self-upgrade", "feature-proposal", f"proposal-{status}"]


def _proposal_issue_body(doc: dict[str, Any]) -> str:
    lines = [
        f"<!-- teamos:feature-proposal:{str(doc.get('proposal_id') or '').strip()} -->",
        "# Feature Proposal Discussion",
        "",
        f"- proposal_id: {doc.get('proposal_id') or ''}",
        f"- repo_locator: {doc.get('repo_locator') or ''}",
        f"- status: {doc.get('status') or ''}",
        f"- version_bump: {doc.get('version_bump') or ''}",
        f"- target_version: {doc.get('target_version') or ''}",
        f"- cooldown_until: {doc.get('cooldown_until') or ''}",
        "",
        "## Summary",
        "",
        str(doc.get("summary") or "").strip() or "(empty)",
        "",
        "## Rationale",
        "",
        str(doc.get("rationale") or "").strip() or "(empty)",
        "",
        "## Work Items",
        "",
    ]
    work_items = list(doc.get("work_items") or [])
    if work_items:
        for raw in work_items:
            item = raw if isinstance(raw, dict) else {}
            lines.append(f"- {str(item.get('title') or '').strip()} [{str(item.get('owner_role') or 'Coding-Agent').strip()}]")
    else:
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "## How To Reply",
            "",
            "- Ask questions directly in this issue; the Team OS discussion agent will reply and adjust the proposal.",
            "- Reply `/approve` or `确认` after you are satisfied.",
            "- Reply `/hold` or `暂缓` to pause the proposal.",
            "- Reply `/reject` or `不做` to cancel the proposal.",
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
    repo_locator = str(proposal.get("repo_locator") or "").strip()
    if not repo_locator:
        return dict(proposal)
    issue_number = _discussion_issue_number(proposal)
    title = _proposal_issue_title(proposal)
    body = _proposal_issue_body(proposal)
    labels = _proposal_issue_labels(proposal)
    try:
        if issue_number > 0:
            issue = update_issue(repo_locator, issue_number, title=title, body=body, labels=labels, state="open")
        else:
            issue = ensure_issue(repo_locator, title=title, body=body, allow_create=True, labels=labels)
            issue = update_issue(repo_locator, issue.number, title=title, body=body, labels=labels, state="open")
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
    from crewai import Agent, Crew, Process, Task

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
    agent = Agent(
        role="Issue Discussion Agent",
        goal="Respond to feature proposal questions, clarify scope, and update the proposal without starting development until the user confirms.",
        backstory="You act like the PM-side feature discussion owner. You answer questions, tighten the feature shape, and only approve development when the user is explicit.",
        llm=llm,
        allow_delegation=False,
        verbose=verbose,
    )
    task = Task(
        name="reply_to_feature_proposal_discussion",
        description=(
            "Read the proposal and the latest user comments.\n"
            "Return JSON matching ProposalDiscussionResponse.\n"
            "Rules:\n"
            "- If the user is only asking questions or suggesting changes, keep action=pending or hold.\n"
            "- Only set action=approve when the user explicitly confirms the feature should proceed.\n"
            "- You may refine title, summary, or version_bump if the user feedback clearly changes the scope.\n"
            "- Keep the reply concise and directly answer the user's latest questions.\n\n"
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
    proposals = list_proposals(lane="feature")
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
            action = explicit_action or str(reply.action or "").strip().lower()
            if action in ("approve", "reject", "hold"):
                proposal = decide_proposal(
                    proposal_id=str(proposal.get("proposal_id") or ""),
                    action=action,
                    title=str(reply.title or "").strip(),
                    summary=str(reply.summary or "").strip(),
                    version_bump=str(reply.version_bump or "").strip(),
                )
            else:
                proposal = _update_proposal_record(
                    str(proposal.get("proposal_id") or ""),
                    title=str(reply.title or "").strip(),
                    summary=str(reply.summary or "").strip(),
                    version_bump=str(reply.version_bump or "").strip(),
                    extra={"status": "PENDING_CONFIRMATION"},
                )
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
    repo_root: Path,
    repo_locator: str,
    project_id: str,
    finding: UpgradeFinding,
    current_version: str,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    proposal_id = _proposal_id_for_finding(repo_locator=repo_locator, repo_root=repo_root, finding=finding)
    state = _read_proposals_state()
    items = state.get("items") if isinstance(state.get("items"), dict) else {}
    existing = items.get(proposal_id) if isinstance(items.get(proposal_id), dict) else {}
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
        "lane": finding.lane,
        "kind": finding.kind,
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
    items[proposal_id] = doc
    state["items"] = items
    _write_proposals_state(state)
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


def _find_existing_task(*, project_id: str, title: str, repo_locator: str, repo_root: Path) -> Optional[dict[str, Any]]:
    d = _task_ledger_dir(project_id)
    if not d.exists():
        return None
    for p in sorted(d.glob("*.yaml")):
        doc = _load_yaml(p)
        if str(doc.get("title") or "").strip() != title:
            continue
        repo = doc.get("repo") or {}
        if not isinstance(repo, dict):
            repo = {}
        locator_matches = str(repo.get("locator") or "").strip() == str(repo_locator or "").strip()
        workdir_matches = str(repo.get("workdir") or "").strip() == str(repo_root)
        if locator_matches or workdir_matches:
            return {"task_id": str(doc.get("id") or "").strip(), "ledger_path": str(p), "doc": doc}
    return None


def _ensure_task_record(
    *,
    repo_root: Path,
    repo_locator: str,
    panel_project_id: str,
    finding: UpgradeFinding,
    work_item: UpgradeWorkItem,
    issue: _IssueRecord,
    proposal_id: str = "",
) -> dict[str, Any]:
    title = _task_title_for_work_item(finding, work_item)
    existing = _find_existing_task(project_id=panel_project_id, title=title, repo_locator=repo_locator, repo_root=repo_root)
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
    work_item = work_item.model_copy(update={"worktree_hint": normalized_worktree_hint})

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
    doc["status"] = "todo"
    doc["workstream_id"] = work_item.workstream_id or finding.workstream_id or str(doc.get("workstream_id") or "general")
    doc["updated_at"] = _utc_now_iso()
    doc["owners"] = [work_item.owner_role]
    doc["owner_role"] = work_item.owner_role
    doc["roles_involved"] = [work_item.owner_role, work_item.review_role, work_item.qa_role]
    doc["need_pm_decision"] = False
    doc["orchestration"] = {
        "engine": "crewai",
        "flow": "self_upgrade",
        "finding_kind": finding.kind,
        "finding_lane": finding.lane,
        "finding_fingerprint": _finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding),
        "proposal_id": proposal_id,
    }
    doc["workflows"] = ["SelfUpgrade"]
    doc["self_upgrade"] = {
        "kind": finding.kind,
        "lane": finding.lane,
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
    doc["execution_policy"] = {
        "issue_only_scope": True,
        "allowed_paths": list(work_item.allowed_paths or []),
        "worktree_hint": normalized_worktree_hint,
        "commit_message_template": f"{task_id}: {work_item.title}",
        "issue_id_required": True,
        "no_extra_optimization": True,
        "review_role": work_item.review_role,
        "qa_role": work_item.qa_role,
    }
    _write_yaml(ledger_path, doc)
    return {"task_id": task_id, "ledger_path": str(ledger_path)}


def _issue_body(*, repo_root: Path, repo_locator: str, finding: UpgradeFinding, work_item: UpgradeWorkItem, fingerprint: str) -> str:
    lines = [
        f"<!-- teamos:self_upgrade:{fingerprint} -->",
        "# Self-Upgrade Finding",
        "",
        f"- kind: {finding.kind}",
        f"- lane: {finding.lane}",
        f"- repo_locator: {repo_locator}",
        f"- repo_root: {repo_root}",
        f"- impact: {finding.impact}",
        f"- owner_role: {work_item.owner_role}",
        f"- review_role: {work_item.review_role}",
        f"- qa_role: {work_item.qa_role}",
        f"- version_bump: {finding.version_bump}",
        f"- target_version: {finding.target_version}",
        "",
        "## Summary",
        "",
        work_item.summary or finding.summary,
        "",
        "## Rationale",
        "",
        finding.rationale or "(none)",
        "",
        "## Execution Policy",
        "",
        f"- worktree_hint: {work_item.worktree_hint or '(none)'}",
        "- issue_only_scope: true",
        "- no_extra_optimization: true",
        "",
        "## Affected Files",
        "",
    ]
    lines.extend([f"- {x}" for x in (work_item.allowed_paths or finding.files or [])] or ["- (not specified)"])
    lines.extend(["", "## Tests", ""])
    lines.extend([f"- {x}" for x in (work_item.tests or finding.tests or [])] or ["- (not specified)"])
    lines.extend(["", "## Acceptance", ""])
    lines.extend([f"- {x}" for x in (work_item.acceptance or finding.acceptance or [])] or ["- (not specified)"])
    lines.append("")
    return "\n".join(lines)


def _ensure_issue_record(*, repo_locator: str, repo_root: Path, finding: UpgradeFinding, work_item: UpgradeWorkItem) -> _IssueRecord:
    if not repo_locator:
        return _IssueRecord(title=_issue_title_for_work_item(repo_root.name, finding, work_item), error="missing_repo_locator")
    title = _issue_title_for_work_item(repo_root.name, finding, work_item)
    fingerprint = _finding_fingerprint(repo_locator=repo_locator, repo_root=repo_root, finding=finding) + "-" + _slug(work_item.title, default="work")
    try:
        issue = ensure_issue(
            repo_locator,
            title=title,
            body=_issue_body(repo_root=repo_root, repo_locator=repo_locator, finding=finding, work_item=work_item, fingerprint=fingerprint),
            allow_create=True,
        )
        return _IssueRecord(title=title, url=str(issue.url or ""))
    except (GitHubAuthError, GitHubIssuesBusError) as e:
        return _IssueRecord(title=title, error=str(e)[:500])


def _sync_panel(*, db, project_id: str) -> dict[str, Any]:
    svc = GitHubProjectsPanelSync(db=db)
    try:
        return svc.sync(project_id=project_id, mode="full", dry_run=False)
    except (GitHubAPIError, GitHubAuthError, PanelMappingError, PanelSyncError) as e:
        return {"ok": False, "error": str(e)[:500], "project_id": project_id}


def _register_agents(*, db, project_id: str, workstream_id: str, task_id: str) -> dict[str, str]:
    return {
        "Product-Manager": db.register_agent(
            role_id="Product-Manager",
            project_id=project_id,
            workstream_id=workstream_id,
            task_id=task_id,
            state="RUNNING",
            current_action="discovering feature opportunities",
        ),
        "Test-Manager": db.register_agent(
            role_id="Test-Manager",
            project_id=project_id,
            workstream_id=workstream_id,
            task_id=task_id,
            state="RUNNING",
            current_action="scanning bugs and test gaps",
        ),
        "Issue-Drafter": db.register_agent(
            role_id="Issue-Drafter",
            project_id=project_id,
            workstream_id=workstream_id,
            task_id=task_id,
            state="RUNNING",
            current_action="splitting work into executable items",
        ),
        "Review-Agent": db.register_agent(
            role_id="Review-Agent",
            project_id=project_id,
            workstream_id=workstream_id,
            task_id=task_id,
            state="RUNNING",
            current_action="checking scope and review gates",
        ),
        "QA-Agent": db.register_agent(
            role_id="QA-Agent",
            project_id=project_id,
            workstream_id=workstream_id,
            task_id=task_id,
            state="RUNNING",
            current_action="reviewing QA and acceptance gates",
        ),
        "Process-Optimization-Analyst": db.register_agent(
            role_id="Process-Optimization-Analyst",
            project_id=project_id,
            workstream_id=workstream_id,
            task_id=task_id,
            state="RUNNING",
            current_action="analyzing process telemetry",
        ),
    }


def _finish_agents(*, db, agent_ids: dict[str, str], state: str, current_action: str) -> None:
    for agent_id in agent_ids.values():
        try:
            db.update_assignment(agent_id=agent_id, state=state, current_action=current_action)
        except Exception:
            pass


def _record_from_materialized_item(
    *,
    repo_root: Path,
    repo_locator: str,
    project_id: str,
    finding: UpgradeFinding,
    work_item: UpgradeWorkItem,
    proposal_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {
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
        repo_root=repo_root,
        repo_locator=repo_locator,
        panel_project_id=project_id,
        finding=finding,
        work_item=work_item,
        issue=issue,
        proposal_id=proposal_id,
    )
    return {
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
    state = _read_proposals_state()
    items = state.get("items") if isinstance(state.get("items"), dict) else {}
    doc = items.get(proposal_id)
    if not isinstance(doc, dict):
        return
    doc["status"] = "MATERIALIZED"
    doc["materialized_at"] = _utc_now_iso()
    doc["updated_at"] = _utc_now_iso()
    items[proposal_id] = doc
    state["items"] = items
    _write_proposals_state(state)


def run_self_upgrade(*, db, spec: Any, actor: str, run_id: str, crewai_info: dict[str, Any]) -> dict[str, Any]:
    repo_root = _target_repo_path(repo_path=str(getattr(spec, "repo_path", "") or ""), project_id=str(getattr(spec, "project_id", "teamos") or "teamos"))
    repo_context = collect_repo_context(repo_root=repo_root, explicit_repo_locator=str(getattr(spec, "repo_locator", "") or ""))
    repo_locator = str(repo_context.get("repo_locator") or "").strip()
    project_id = _panel_project_id(str(getattr(spec, "project_id", "teamos") or "teamos"))
    workstream_id = str(getattr(spec, "workstream_id", "") or "general").strip() or "general"
    force = bool(getattr(spec, "force", False))
    dry_run = bool(getattr(spec, "dry_run", False))
    trigger = str(getattr(spec, "trigger", "") or "manual").strip() or "manual"
    max_findings = max(1, min(int(os.getenv("TEAMOS_SELF_UPGRADE_MAX_FINDINGS", "3") or "3"), 10))
    run_started_at = _utc_now_iso()

    should_skip, skip_reason = _should_skip(repo_root=repo_root, force=force)
    if should_skip:
        payload = {
            "ok": True,
            "skipped": True,
            "reason": skip_reason,
            "run_id": run_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "trigger": trigger,
            "crewai": crewai_info,
        }
        _merge_state_last_run({"ts": _utc_now_iso(), "repo_root": str(repo_root), "repo_locator": repo_locator, "status": "SKIPPED", "reason": skip_reason})
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
            {
                "ts": _utc_now_iso(),
                "repo_root": str(repo_root),
                "repo_locator": repo_locator,
                "status": "FAILED",
                "error": err_text[:500],
            },
            backoff_until=backoff_until,
        )
        _append_run_history(
            {
                "ts": _utc_now_iso(),
                "run_id": run_id,
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
        work_items = list(finding.work_items or []) or _default_work_items(repo_root=repo_root, finding=finding)
        if finding.lane == "bug":
            for work_item in work_items:
                record = _record_from_materialized_item(
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
            repo_root=repo_root,
            repo_locator=repo_locator,
            project_id=project_id,
            finding=finding,
            current_version=current_version,
        )
        if finding.lane == "feature":
            proposal = _ensure_proposal_discussion_issue(proposal)
        proposal_id = str(proposal.get("proposal_id") or "")
        status = str(proposal.get("status") or "").strip().upper()
        due = _proposal_due(proposal)
        should_materialize = False
        if finding.lane == "feature":
            should_materialize = status == "APPROVED" and due
        else:
            should_materialize = status not in ("REJECTED", "HOLD", "MATERIALIZED") and due

        if should_materialize:
            for work_item in work_items:
                record = _record_from_materialized_item(
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
        "actor": actor,
        "trigger": trigger,
        "repo_context": repo_context,
        "plan": plan.model_dump(),
        "records": records,
        "pending_proposals": pending_proposals,
        "panel_sync": panel_sync,
        "crewai": crewai_info,
        "crew_debug": crew_debug,
    }
    report_path = _reports_dir() / f"{_ts_compact_utc()}-{_slug(repo_root.name)}.json"
    _write_json(report_path, report)
    _merge_state_last_run(
        {
            "ts": _utc_now_iso(),
            "run_id": run_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "status": "DONE",
            "records": len(records),
            "pending_proposals": len(pending_proposals),
            "report_path": str(report_path),
        },
    )
    _append_run_history(
        {
            "ts": _utc_now_iso(),
            "run_id": run_id,
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
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "records": len(records),
            "pending_proposals": len(pending_proposals),
            "panel_sync": panel_sync,
            "report_path": str(report_path),
        },
    )
    return {
        "ok": True,
        "run_id": run_id,
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
        "report_path": str(report_path),
        "crewai": crewai_info,
        "dry_run": dry_run,
        "write_delegate": {
            "write_mode": "crewai_self_upgrade",
            "writer": "crewai_agents",
            "truth_sources": ["task_ledger", "github_issues", "github_projects"],
            "target_repo": repo_locator or str(repo_root),
        },
    }
