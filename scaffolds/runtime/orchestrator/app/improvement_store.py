from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from . import workspace_store
from .runtime_state_store import delete_doc, get_doc, get_state, list_docs, put_doc, put_state
from .state_store import team_os_root


TARGET_NAMESPACE = "improvement_target"
PROPOSAL_NAMESPACE = "improvement_proposal"
DELIVERY_TASK_NAMESPACE = "improvement_delivery_task"
MILESTONE_NAMESPACE = "improvement_milestone"
STATE_NAMESPACE = "improvement_state"
REPORT_NAMESPACE = "improvement_report"


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(text: str, *, default: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return s or default


def _safe_project_id(raw: str) -> str:
    base = _slug(raw, default="project").replace("-", "_")
    return base[:64] or "project"


def _git_dir(repo_root: Path) -> Optional[Path]:
    dotgit = repo_root / ".git"
    if dotgit.is_dir():
        return dotgit
    if dotgit.is_file():
        raw = dotgit.read_text(encoding="utf-8", errors="replace").strip()
        if raw.startswith("gitdir:"):
            return (repo_root / raw.split(":", 1)[1].strip()).resolve()
    return None


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
    return str(parser.get('remote "origin"', "url", fallback="") or "").strip()


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


def _target_id_for(*, repo_locator: str, repo_root: str, repo_url: str, project_id: str) -> str:
    if str(repo_locator or "").strip():
        return _slug(str(repo_locator).replace("/", "-"), default="target")
    if str(repo_root or "").strip():
        return _slug(Path(str(repo_root)).name, default="target")
    if str(repo_url or "").strip():
        return _slug(str(repo_url), default="target")[:48]
    return _slug(str(project_id or "") or "target", default="target")


def _run_git(args: list[str], *, cwd: Optional[Path] = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {err[:400]}")
    return (proc.stdout or "").strip()


def _normalize_target(raw: dict[str, Any]) -> dict[str, Any]:
    now = _utc_now_iso()
    repo_root = str(raw.get("repo_root") or raw.get("repo_path") or "").strip()
    repo_url = str(raw.get("repo_url") or "").strip()
    repo_locator = str(raw.get("repo_locator") or "").strip()
    project_id = str(raw.get("project_id") or "teamos").strip() or "teamos"
    target_id = str(raw.get("target_id") or _target_id_for(repo_locator=repo_locator, repo_root=repo_root, repo_url=repo_url, project_id=project_id)).strip()
    display_name = str(raw.get("display_name") or repo_locator or Path(repo_root).name or target_id).strip()
    if not repo_locator and repo_root:
        repo_locator = _parse_repo_locator(_origin_url(Path(repo_root).expanduser().resolve()))
    checkout_policy = str(raw.get("checkout_policy") or ("existing" if repo_root else ("clone" if repo_url else "existing"))).strip() or "existing"
    workstream_id = str(raw.get("workstream_id") or "general").strip() or "general"
    doc = {
        "target_id": target_id,
        "project_id": project_id,
        "display_name": display_name,
        "team_template": str(raw.get("team_template") or "improvement").strip() or "improvement",
        "repo_root": repo_root,
        "repo_url": repo_url,
        "repo_locator": repo_locator,
        "default_branch": str(raw.get("default_branch") or "").strip(),
        "checkout_policy": checkout_policy,
        "enabled": bool(raw.get("enabled", True)),
        "auto_discovery": bool(raw.get("auto_discovery", False)),
        "auto_delivery": bool(raw.get("auto_delivery", False)),
        "ship_enabled": bool(raw.get("ship_enabled", False)),
        "workstream_id": workstream_id,
        "version_policy": dict(raw.get("version_policy") or {}),
        "docs_policy": dict(raw.get("docs_policy") or {}),
        "notification_policy": dict(raw.get("notification_policy") or {}),
        "metadata": dict(raw.get("metadata") or {}),
        "created_at": str(raw.get("created_at") or now),
        "updated_at": now,
    }
    return doc


def upsert_target(raw: dict[str, Any]) -> dict[str, Any]:
    doc = _normalize_target(raw)
    put_doc(
        TARGET_NAMESPACE,
        doc["target_id"],
        project_id=str(doc.get("project_id") or "teamos"),
        scope_id=str(doc.get("target_id") or ""),
        state="enabled" if bool(doc.get("enabled")) else "disabled",
        category=str(doc.get("team_template") or "improvement"),
        value=doc,
    )
    return doc


def ensure_target(*, project_id: str = "teamos", target_id: str = "", repo_path: str = "", repo_locator: str = "", repo_url: str = "") -> dict[str, Any]:
    tid = str(target_id or "").strip()
    if tid:
        doc = get_doc(TARGET_NAMESPACE, tid)
        if doc:
            return dict(doc)
    repo_root = ""
    if str(repo_path or "").strip():
        repo_root = str(Path(str(repo_path)).expanduser().resolve())
    elif str(project_id or "").strip() == "teamos":
        repo_root = str(team_os_root())
    else:
        candidate = workspace_store.project_repo_dir(project_id)
        if candidate.exists():
            repo_root = str(candidate.resolve())
    locator = str(repo_locator or "").strip()
    if not locator and repo_root:
        locator = _parse_repo_locator(_origin_url(Path(repo_root)))
    url = str(repo_url or "").strip() or (_origin_url(Path(repo_root)) if repo_root else "")
    return upsert_target(
        {
            "target_id": tid or _target_id_for(repo_locator=locator, repo_root=repo_root, repo_url=url, project_id=project_id),
            "project_id": str(project_id or "teamos").strip() or "teamos",
            "repo_root": repo_root,
            "repo_url": url,
            "repo_locator": locator,
            "enabled": True,
            "auto_discovery": bool(str(project_id or "teamos").strip() == "teamos"),
            "display_name": locator or Path(repo_root).name or tid or "target",
        }
    )


def materialize_target_repo(target: dict[str, Any], *, fetch: bool = True) -> dict[str, Any]:
    doc = _normalize_target(target)
    target_id = str(doc.get("target_id") or "").strip()
    project_id = str(doc.get("project_id") or "teamos").strip() or "teamos"
    repo_root_raw = str(doc.get("repo_root") or "").strip()
    repo_url = str(doc.get("repo_url") or "").strip()
    checkout_policy = str(doc.get("checkout_policy") or "").strip() or ("clone" if repo_url else "existing")

    scaffold = workspace_store.ensure_target_scaffold(target_id, project_id=project_id)
    scaffold_repo_root = Path(str(scaffold.get("repo_dir") or "")).resolve()
    project_repo_root = workspace_store.project_repo_dir(project_id).resolve()
    repo_candidates: list[Path] = []
    for raw in (
        repo_root_raw,
        str(scaffold_repo_root),
        str(project_repo_root),
    ):
        candidate_raw = str(raw or "").strip()
        if not candidate_raw:
            continue
        candidate = Path(candidate_raw).expanduser().resolve()
        if candidate not in repo_candidates:
            repo_candidates.append(candidate)

    repo_root: Path = scaffold_repo_root
    for candidate in repo_candidates:
        if candidate.exists() and _git_dir(candidate) is not None:
            repo_root = candidate
            break
    else:
        if repo_root_raw:
            repo_root = Path(repo_root_raw).expanduser().resolve()

    git_dir = _git_dir(repo_root) if repo_root.exists() else None
    if git_dir is None and repo_url:
        if checkout_policy not in ("clone", "existing"):
            raise RuntimeError(f"unsupported checkout_policy={checkout_policy}")
        if repo_root.exists() and any(repo_root.iterdir()):
            raise RuntimeError(f"target repo root is not empty: {repo_root}")
        repo_root.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--origin", "origin", repo_url, str(repo_root)])
        git_dir = _git_dir(repo_root)

    if git_dir is None:
        raise RuntimeError(f"target repository is unavailable: target_id={target_id} repo_root={repo_root}")

    if fetch:
        try:
            _run_git(["fetch", "--prune", "origin"], cwd=repo_root)
        except Exception:
            pass

    default_branch = str(doc.get("default_branch") or "").strip()
    if not default_branch:
        try:
            ref = _run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo_root)
            if ref.startswith("origin/"):
                default_branch = ref.split("/", 1)[1].strip()
        except Exception:
            pass
    if not default_branch:
        try:
            default_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root).strip()
        except Exception:
            default_branch = ""

    origin_url = _origin_url(repo_root) or repo_url
    repo_locator = str(doc.get("repo_locator") or "").strip() or _parse_repo_locator(origin_url)
    updated = dict(doc)
    updated.update(
        {
            "repo_root": str(repo_root),
            "repo_url": origin_url,
            "repo_locator": repo_locator,
            "default_branch": default_branch,
        }
    )
    return upsert_target(updated)


def get_target(target_id: str) -> Optional[dict[str, Any]]:
    doc = get_doc(TARGET_NAMESPACE, str(target_id or "").strip())
    return dict(doc) if isinstance(doc, dict) else None


def list_targets(*, project_id: str = "", enabled_only: bool = False) -> list[dict[str, Any]]:
    state = "enabled" if enabled_only else ""
    items = list_docs(TARGET_NAMESPACE, project_id=str(project_id or ""), state=state, limit=500)
    return sorted(items, key=lambda x: (str(x.get("updated_at") or ""), str(x.get("target_id") or "")), reverse=True)


def load_target_state(target_id: str) -> dict[str, Any]:
    return get_state(STATE_NAMESPACE, str(target_id or "").strip(), default={})


def save_target_state(target_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    state_key = str(target_id or "").strip()
    current = load_target_state(state_key)
    current.update(dict(payload or {}))
    put_state(STATE_NAMESPACE, state_key, current)
    return current


def append_target_history(target_id: str, entry: dict[str, Any], *, keep: int = 30) -> dict[str, Any]:
    state = load_target_state(target_id)
    rows = list(state.get("history") or []) if isinstance(state.get("history"), list) else []
    rows.append(dict(entry or {}))
    state["history"] = rows[-max(1, int(keep)) :]
    put_state(STATE_NAMESPACE, str(target_id or "").strip(), state)
    return state


def merge_target_last_run(target_id: str, last_run: dict[str, Any], *, backoff_until: str = "") -> dict[str, Any]:
    state = load_target_state(target_id)
    state["last_run"] = dict(last_run or {})
    if str(backoff_until or "").strip():
        state["backoff_until"] = str(backoff_until).strip()
    else:
        state.pop("backoff_until", None)
    put_state(STATE_NAMESPACE, str(target_id or "").strip(), state)
    return state


def upsert_proposal(doc: dict[str, Any]) -> dict[str, Any]:
    proposal_id = str(doc.get("proposal_id") or "").strip()
    if not proposal_id:
        raise RuntimeError("proposal_id is required")
    project_id = str(doc.get("project_id") or "teamos").strip() or "teamos"
    target_id = str(doc.get("target_id") or "").strip() or _target_id_for(
        repo_locator=str(doc.get("repo_locator") or "").strip(),
        repo_root=str(doc.get("repo_root") or "").strip(),
        repo_url=str(doc.get("repo_url") or "").strip(),
        project_id=project_id,
    )
    payload = dict(doc)
    payload["target_id"] = target_id
    payload["team_id"] = str(payload.get("team_id") or _team_id_from_flow(((payload.get("orchestration") or {}) if isinstance(payload.get("orchestration"), dict) else {}).get("flow")) or "").strip()
    put_doc(
        PROPOSAL_NAMESPACE,
        proposal_id,
        project_id=project_id,
        scope_id=target_id,
        state=str(payload.get("status") or "").strip(),
        category=str(payload.get("lane") or "").strip(),
        value=payload,
    )
    return payload


def get_proposal(proposal_id: str) -> Optional[dict[str, Any]]:
    doc = get_doc(PROPOSAL_NAMESPACE, str(proposal_id or "").strip())
    return dict(doc) if isinstance(doc, dict) else None


def list_proposals(*, target_id: str = "", project_id: str = "", lane: str = "", status: str = "", team_id: str = "") -> list[dict[str, Any]]:
    items = list_docs(
        PROPOSAL_NAMESPACE,
        project_id=str(project_id or ""),
        scope_id=str(target_id or ""),
        state=str(status or ""),
        category=str(lane or ""),
        limit=5000,
    )
    lane_filter = str(lane or "").strip().lower()
    status_filter = str(status or "").strip().upper()
    team_filter = str(team_id or "").strip()
    out: list[dict[str, Any]] = []
    for doc in items:
        if team_filter and str(doc.get("team_id") or "").strip() != team_filter:
            continue
        if lane_filter and str(doc.get("lane") or "").strip().lower() != lane_filter:
            continue
        if status_filter and str(doc.get("status") or "").strip().upper() != status_filter:
            continue
        out.append(doc)
    return sorted(out, key=lambda x: (str(x.get("updated_at") or ""), str(x.get("proposal_id") or "")), reverse=True)


def update_proposal(proposal_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_proposal(proposal_id)
    if not current:
        raise RuntimeError(f"proposal not found: {proposal_id}")
    current.update(dict(patch or {}))
    return upsert_proposal(current)


def upsert_delivery_task(doc: dict[str, Any]) -> dict[str, Any]:
    task_id = str(doc.get("id") or doc.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("task id is required")
    orchestration = doc.get("orchestration") or {}
    if not isinstance(orchestration, dict):
        orchestration = {}
    team = doc.get("team")
    if not isinstance(team, dict):
        team = {}
    target_id = str((doc.get("target") or {}).get("target_id") if isinstance(doc.get("target"), dict) else "").strip() or str(doc.get("target_id") or "").strip()
    if not target_id:
        target_id = _target_id_for(
            repo_locator=str(((doc.get("repo") or {}) if isinstance(doc.get("repo"), dict) else {}).get("locator") or "").strip(),
            repo_root=str(((doc.get("repo") or {}) if isinstance(doc.get("repo"), dict) else {}).get("source_workdir") or ((doc.get("repo") or {}) if isinstance(doc.get("repo"), dict) else {}).get("workdir") or "").strip(),
            repo_url="",
            project_id=str(doc.get("project_id") or "teamos"),
        )
    payload = dict(doc)
    payload["target_id"] = target_id
    payload["team_id"] = str(payload.get("team_id") or team.get("team_id") or _team_id_from_flow(orchestration.get("flow")) or "").strip()
    put_doc(
        DELIVERY_TASK_NAMESPACE,
        task_id,
        project_id=str(doc.get("project_id") or "teamos"),
        scope_id=target_id,
        state=str(doc.get("status") or doc.get("state") or "").strip(),
        category=str(team.get("lane") or orchestration.get("finding_lane") or "").strip(),
        value=payload,
    )
    return payload


def get_delivery_task(task_id: str) -> Optional[dict[str, Any]]:
    doc = get_doc(DELIVERY_TASK_NAMESPACE, str(task_id or "").strip())
    return dict(doc) if isinstance(doc, dict) else None


def list_delivery_tasks(*, target_id: str = "", project_id: str = "", status: str = "", team_id: str = "") -> list[dict[str, Any]]:
    items = list_docs(
        DELIVERY_TASK_NAMESPACE,
        project_id=str(project_id or ""),
        scope_id=str(target_id or ""),
        state=str(status or ""),
        limit=5000,
    )
    out: list[dict[str, Any]] = []
    status_filter = str(status or "").strip().lower()
    team_filter = str(team_id or "").strip()
    for doc in items:
        if team_filter and str(doc.get("team_id") or "").strip() != team_filter:
            continue
        st = str(doc.get("status") or doc.get("state") or "").strip().lower()
        if status_filter and st != status_filter:
            continue
        out.append(doc)
    return sorted(out, key=lambda x: (str(x.get("updated_at") or ""), str(x.get("id") or x.get("task_id") or "")), reverse=True)


def delete_delivery_task(task_id: str) -> None:
    delete_doc(DELIVERY_TASK_NAMESPACE, str(task_id or "").strip())


def upsert_milestone(doc: dict[str, Any]) -> dict[str, Any]:
    milestone_id = str(doc.get("milestone_id") or "").strip()
    if not milestone_id:
        raise RuntimeError("milestone_id is required")
    project_id = str(doc.get("project_id") or "teamos").strip() or "teamos"
    target_id = str(doc.get("target_id") or "").strip() or _target_id_for(
        repo_locator=str(doc.get("repo_locator") or "").strip(),
        repo_root="",
        repo_url="",
        project_id=project_id,
    )
    payload = dict(doc)
    payload["target_id"] = target_id
    put_doc(
        MILESTONE_NAMESPACE,
        milestone_id,
        project_id=project_id,
        scope_id=target_id,
        state=str(payload.get("state") or "draft").strip(),
        category=str(payload.get("release_line") or "").strip(),
        value=payload,
    )
    return payload


def list_milestones(*, target_id: str = "", project_id: str = "") -> list[dict[str, Any]]:
    items = list_docs(MILESTONE_NAMESPACE, project_id=str(project_id or ""), scope_id=str(target_id or ""), limit=1000)
    return sorted(items, key=lambda x: (str(x.get("target_date") or "9999-12-31"), str(x.get("title") or x.get("milestone_id") or "")))


def save_report(*, target_id: str, project_id: str, report: dict[str, Any]) -> dict[str, Any]:
    report_id = str(report.get("run_id") or hashlib.sha1(str(report).encode("utf-8")).hexdigest()[:12]).strip()
    payload = dict(report)
    payload["target_id"] = str(target_id or "").strip()
    report_state = str(payload.get("state") or "done").strip() or "done"
    put_doc(
        REPORT_NAMESPACE,
        report_id,
        project_id=str(project_id or "teamos"),
        scope_id=str(target_id or "").strip(),
        state=report_state,
        category="report",
        value=payload,
    )
    return payload


def get_report(report_id: str) -> Optional[dict[str, Any]]:
    doc = get_doc(REPORT_NAMESPACE, str(report_id or "").strip())
    return dict(doc) if isinstance(doc, dict) else None


def list_reports(*, target_id: str = "", project_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    items = list_docs(
        REPORT_NAMESPACE,
        project_id=str(project_id or ""),
        scope_id=str(target_id or ""),
        limit=max(1, min(int(limit or 100), 1000)),
    )
    return sorted(items, key=lambda x: str(x.get("ts") or x.get("run_id") or ""), reverse=True)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "__dict__"):
        return dict(getattr(row, "__dict__") or {})
    out: dict[str, Any] = {}
    for key in (
        "run_id",
        "project_id",
        "workstream_id",
        "objective",
        "state",
        "created_at",
        "updated_at",
    ):
        if hasattr(row, key):
            out[key] = getattr(row, key)
    return out


def _serialize_event_row(row: Any) -> dict[str, Any]:
    return {
        "id": int(getattr(row, "id", 0) or 0),
        "ts": str(getattr(row, "ts", "") or ""),
        "event_type": str(getattr(row, "event_type", "") or ""),
        "actor": str(getattr(row, "actor", "") or ""),
        "project_id": str(getattr(row, "project_id", "") or ""),
        "workstream_id": str(getattr(row, "workstream_id", "") or ""),
        "payload": dict(getattr(row, "payload", {}) or {}),
    }


def _events_for_run(db: Any, run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    target_run_id = str(run_id or "").strip()
    if not target_run_id:
        return []
    rows: list[dict[str, Any]] = []
    after_id = 0
    max_rows = max(1, min(int(limit or 200), 1000))
    while len(rows) < max_rows:
        batch = db.list_events(after_id=after_id, limit=1000)
        if not batch:
            break
        for item in batch:
            after_id = max(after_id, int(getattr(item, "id", 0) or 0))
            payload = getattr(item, "payload", {}) if isinstance(getattr(item, "payload", {}), dict) else {}
            if str(payload.get("run_id") or "").strip() != target_run_id:
                continue
            rows.append(_serialize_event_row(item))
            if len(rows) >= max_rows:
                break
        if len(batch) < 1000:
            break
    return rows[-max_rows:]


def _team_id_from_flow(flow: Any) -> str:
    raw = str(flow or "").strip()
    if raw.startswith("team:"):
        return raw.split(":", 1)[1].strip()
    return ""


def _run_team_id(*, db: Any, run_id: str) -> str:
    for item in _events_for_run(db, run_id, limit=500):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        team_id = _team_id_from_flow(payload.get("flow"))
        if team_id:
            return team_id
        team_id = str(payload.get("team_id") or "").strip()
        if team_id:
            return team_id
    return ""


def team_logs_dir(project_id: str, team_id: str) -> Path:
    workspace_store.ensure_project_scaffold(str(project_id or "teamos"))
    root = workspace_store.logs_team_dir(str(project_id or "teamos"), str(team_id or "team"))
    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    return runs


def team_run_log_paths(*, project_id: str, team_id: str, run_id: str) -> dict[str, str]:
    base = team_logs_dir(project_id, team_id)
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(run_id or "").strip()) or "run"
    return {
        "json_path": str((base / f"{safe_run_id}.json").resolve()),
        "markdown_path": str((base / f"{safe_run_id}.md").resolve()),
    }


def build_team_run_logs_payload(*, db: Any, run_id: str, limit: int = 200) -> dict[str, Any]:
    row = db.get_run(run_id)
    if not row:
        raise KeyError(f"run_not_found:{run_id}")
    run = _row_to_dict(row)
    report = get_report(run_id) or {}
    crew_debug = report.get("crew_debug") if isinstance(report.get("crew_debug"), dict) else {}
    agent_logs = []
    for item in list(crew_debug.get("task_outputs") or []):
        if not isinstance(item, dict):
            continue
        agent_logs.append(
            {
                "stage": "planning",
                "task_name": str(item.get("name") or "").strip(),
                "agent": str(item.get("agent") or "").strip(),
                "raw": str(item.get("raw") or ""),
            }
        )
    plan = report.get("plan") if isinstance(report.get("plan"), dict) else {}
    project_id = str(run.get("project_id") or report.get("project_id") or "teamos").strip() or "teamos"
    team_id = str(report.get("team_id") or _run_team_id(db=db, run_id=run_id) or "team").strip() or "team"
    payload = {
        "run": run,
        "team_id": team_id,
        "report_available": bool(report),
        "summary": str(report.get("summary") or plan.get("summary") or ""),
        "target_id": str(report.get("target_id") or ""),
        "bug_findings": int(report.get("bug_findings") or 0),
        "records": list(report.get("records") or []),
        "pending_proposals": list(report.get("pending_proposals") or []),
        "planning_agent_logs": agent_logs,
        "events": _events_for_run(db, run_id, limit=limit),
        "saved_logs": team_run_log_paths(project_id=project_id, team_id=team_id, run_id=run_id),
    }
    return payload


def _compact_value(value: Any, *, max_len: int = 160) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value or "")
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _event_summary(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("stage", "reason", "lane", "workflow_id", "title", "status", "records", "bug_findings", "proposal_id", "target_id"):
        value = payload.get(key)
        if value in ("", None, [], {}):
            continue
        parts.append(f"{key}={_compact_value(value)}")
    if not parts and payload:
        parts.append(_compact_value(payload))
    return "; ".join(parts)


def _display_event_type(event_type: Any) -> str:
    return str(event_type or "").strip()


def render_team_run_logs_markdown(payload: dict[str, Any]) -> str:
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    saved_logs = payload.get("saved_logs") if isinstance(payload.get("saved_logs"), dict) else {}
    team_id = str(payload.get("team_id") or "").strip() or "team"
    lines: list[str] = [
        f"# Team Run `{run.get('run_id', '')}`",
        "",
        "## Summary",
        "",
        f"- team_id: `{team_id}`",
        f"- state: `{run.get('state', '')}`",
        f"- project_id: `{run.get('project_id', '')}`",
        f"- workstream_id: `{run.get('workstream_id', '')}`",
        f"- objective: `{run.get('objective', '')}`",
        f"- report_available: `{bool(payload.get('report_available'))}`",
        f"- bug_findings: `{int(payload.get('bug_findings') or 0)}`",
        f"- records: `{len(list(payload.get('records') or []))}`",
        f"- pending_proposals: `{len(list(payload.get('pending_proposals') or []))}`",
    ]
    summary = str(payload.get("summary") or "").strip()
    if summary:
        lines.extend(["", f"> {summary}"])
    if saved_logs:
        lines.extend(
            [
                "",
                "## Saved Artifacts",
                "",
                f"- markdown: `{saved_logs.get('markdown_path', '')}`",
                f"- json: `{saved_logs.get('json_path', '')}`",
            ]
        )
    lines.extend(["", "## Planning Agent Logs", ""])
    agent_logs = list(payload.get("planning_agent_logs") or [])
    if not agent_logs:
        lines.append("_No planning agent logs captured._")
    else:
        for idx, item in enumerate(agent_logs, start=1):
            agent = str(item.get("agent") or "").strip() or "agent"
            task_name = str(item.get("task_name") or "").strip() or "task"
            raw = str(item.get("raw") or "").rstrip() or "(empty)"
            lines.extend(
                [
                    f"### {idx}. {agent} :: {task_name}",
                    "",
                    "```text",
                    raw,
                    "```",
                    "",
                ]
            )
    lines.extend(["## Events", "", "| ts | event | actor | details |", "| --- | --- | --- | --- |"])
    events = list(payload.get("events") or [])
    if not events:
        lines.append("| - | - | - | No runtime events captured |")
    else:
        for item in events:
            event_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            details = _event_summary(event_payload).replace("|", "\\|")
            ts = str(item.get("ts") or "").replace("|", "\\|")
            event_type = _display_event_type(item.get("event_type")).replace("|", "\\|")
            actor = str(item.get("actor") or "").replace("|", "\\|")
            lines.append(f"| {ts} | {event_type} | {actor} | {details or '-'} |")
    return "\n".join(lines).rstrip() + "\n"


def persist_team_run_logs(*, db: Any, run_id: str, limit: int = 200) -> dict[str, Any]:
    payload = build_team_run_logs_payload(db=db, run_id=run_id, limit=limit)
    saved_logs = payload.get("saved_logs") if isinstance(payload.get("saved_logs"), dict) else {}
    json_path = Path(str(saved_logs.get("json_path") or "")).resolve()
    markdown_path = Path(str(saved_logs.get("markdown_path") or "")).resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_team_run_logs_markdown(payload), encoding="utf-8")
    return payload
