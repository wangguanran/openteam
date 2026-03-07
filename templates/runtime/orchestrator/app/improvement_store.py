from __future__ import annotations

import configparser
import hashlib
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
    repo_root = str(raw.get("repo_root") or "").strip()
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
    repo_root_raw = str(doc.get("repo_root") or "").strip()
    repo_url = str(doc.get("repo_url") or "").strip()
    checkout_policy = str(doc.get("checkout_policy") or "").strip() or ("clone" if repo_url else "existing")

    if repo_root_raw:
        repo_root = Path(repo_root_raw).expanduser().resolve()
    else:
        scaffold = workspace_store.ensure_target_scaffold(target_id)
        repo_root = Path(str(scaffold.get("repo_dir") or "")).resolve()

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


def list_proposals(*, target_id: str = "", project_id: str = "", lane: str = "", status: str = "") -> list[dict[str, Any]]:
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
    out: list[dict[str, Any]] = []
    for doc in items:
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
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        su = {}
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
    put_doc(
        DELIVERY_TASK_NAMESPACE,
        task_id,
        project_id=str(doc.get("project_id") or "teamos"),
        scope_id=target_id,
        state=str(doc.get("status") or doc.get("state") or "").strip(),
        category=str(su.get("lane") or orchestration.get("finding_lane") or "").strip(),
        value=payload,
    )
    return payload


def get_delivery_task(task_id: str) -> Optional[dict[str, Any]]:
    doc = get_doc(DELIVERY_TASK_NAMESPACE, str(task_id or "").strip())
    return dict(doc) if isinstance(doc, dict) else None


def list_delivery_tasks(*, target_id: str = "", project_id: str = "", status: str = "") -> list[dict[str, Any]]:
    items = list_docs(
        DELIVERY_TASK_NAMESPACE,
        project_id=str(project_id or ""),
        scope_id=str(target_id or ""),
        state=str(status or ""),
        limit=5000,
    )
    out: list[dict[str, Any]] = []
    status_filter = str(status or "").strip().lower()
    for doc in items:
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
    put_doc(REPORT_NAMESPACE, report_id, project_id=str(project_id or "teamos"), scope_id=str(target_id or "").strip(), state="done", category="report", value=payload)
    return payload
