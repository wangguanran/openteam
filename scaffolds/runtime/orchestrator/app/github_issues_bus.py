import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from .github_projects_client import GitHubAuthError, resolve_github_token


class GitHubIssuesBusError(Exception):
    pass


def _norm(s: str) -> str:
    return (s or "").strip()


def _api_url() -> str:
    # REST API base
    return "https://api.github.com"


def _request(method: str, url: str, *, payload: Optional[dict[str, Any]] = None, timeout_sec: int = 20) -> Any:
    tok = resolve_github_token()
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {tok}",
        "User-Agent": "teamos-control-plane",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise GitHubIssuesBusError(f"GitHub REST HTTP {e.code}: {body[:800]}") from e
    except urllib.error.URLError as e:
        raise GitHubIssuesBusError(f"GitHub REST request failed: {e}") from e


def _parse_repo(repo: str) -> tuple[str, str]:
    repo = _norm(repo)
    if "/" not in repo:
        raise GitHubIssuesBusError(f"invalid repo locator: {repo} (expected owner/name)")
    owner, name = repo.split("/", 1)
    return owner.strip(), name.strip()


_UNSET = object()

_LABEL_DEFAULTS: dict[str, tuple[str, str]] = {
    "teamos": ("1d76db", "Team OS managed issue"),
    "type:feature": ("1d76db", "Feature work item"),
    "type:bug": ("d73a4a", "Bug fix work item"),
    "type:process": ("6f42c1", "Process improvement item"),
    "type:quality": ("0052cc", "Code quality improvement item"),
    "stage:proposal": ("fbca04", "Pending proposal discussion"),
    "stage:queued": ("cfd3d7", "Queued for implementation"),
    "stage:audit": ("fbca04", "Issue audit in progress"),
    "stage:coding": ("fbca04", "Implementation in progress"),
    "stage:review": ("5319e7", "Under review"),
    "stage:qa": ("0e8a16", "In QA verification"),
    "stage:docs": ("1d76db", "Documentation synchronization in progress"),
    "stage:release": ("bfdadc", "Ready for release handoff"),
    "stage:needs-clarification": ("b60205", "Needs clarification before delivery"),
    "stage:blocked": ("b60205", "Blocked and needs intervention"),
    "stage:done": ("0e8a16", "Closed and completed"),
    "proposal:pending-confirmation": ("fbca04", "Awaiting user confirmation"),
    "proposal:approved": ("0e8a16", "Proposal approved"),
    "proposal:hold": ("d4c5f9", "Proposal on hold"),
    "proposal:rejected": ("b60205", "Proposal rejected"),
    "proposal:materialized": ("0e8a16", "Proposal materialized into tasks"),
    "version:major": ("b60205", "Major version impact"),
    "version:minor": ("1d76db", "Minor version impact"),
    "version:patch": ("fbca04", "Patch version impact"),
    "version:none": ("cfd3d7", "No version impact"),
}


@dataclass(frozen=True)
class IssueRef:
    number: int
    url: str
    title: str
    body: str
    state: str = ""
    labels: list[str] = field(default_factory=list)


def search_issue_by_title(repo: str, title: str) -> Optional[IssueRef]:
    owner, name = _parse_repo(repo)
    q = f'repo:{owner}/{name} in:title "{title}"'
    url = _api_url() + "/search/issues?" + urllib.parse.urlencode({"q": q, "per_page": 10})
    data = _request("GET", url, payload=None, timeout_sec=20)
    items = data.get("items") or []
    for it in items:
        if str(it.get("title") or "").strip() == title.strip():
            num = int(it.get("number") or 0)
            if num <= 0:
                continue
            # Fetch full issue to get body.
            return get_issue(repo, num)
    return None


def search_issue_by_marker(repo: str, marker: str) -> Optional[IssueRef]:
    owner, name = _parse_repo(repo)
    mk = _norm(marker)
    if not mk:
        return None
    q = f'repo:{owner}/{name} in:body "{mk}"'
    url = _api_url() + "/search/issues?" + urllib.parse.urlencode({"q": q, "per_page": 10})
    data = _request("GET", url, payload=None, timeout_sec=20)
    items = data.get("items") or []
    for it in items:
        num = int(it.get("number") or 0)
        if num <= 0:
            continue
        full = get_issue(repo, num)
        if mk in str(full.body or ""):
            return full
    return None


def list_labels(repo: str) -> list[str]:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/labels?per_page=100"
    data = _request("GET", url, payload=None, timeout_sec=20)
    if not isinstance(data, list):
        return []
    out = [str((x or {}).get("name") or "").strip() for x in data if str((x or {}).get("name") or "").strip()]
    return sorted(set(out))


def _label_spec(name: str) -> tuple[str, str]:
    lbl = _norm(name)
    if lbl in _LABEL_DEFAULTS:
        return _LABEL_DEFAULTS[lbl]
    if lbl.startswith("source:"):
        return ("0e8a16", f"Created or updated by Team OS {lbl.split(':', 1)[1]}")
    if lbl.startswith("module:"):
        return ("bfd4f2", f"Module scope: {lbl.split(':', 1)[1]}")
    if lbl.startswith("milestone:"):
        return ("bfdadc", f"Milestone scope: {lbl.split(':', 1)[1]}")
    return ("cfd3d7", "")


def ensure_labels(repo: str, labels: list[str]) -> None:
    wanted = sorted({str(x).strip() for x in (labels or []) if str(x).strip()})
    if not wanted:
        return
    owner, name = _parse_repo(repo)
    existing = set(list_labels(repo))
    for label in wanted:
        if label in existing:
            continue
        color, description = _label_spec(label)
        payload: dict[str, Any] = {"name": label, "color": color}
        if description:
            payload["description"] = description
        try:
            _request("POST", _api_url() + f"/repos/{owner}/{name}/labels", payload=payload, timeout_sec=20)
            existing.add(label)
        except GitHubIssuesBusError:
            refreshed = set(list_labels(repo))
            if label not in refreshed:
                raise
            existing = refreshed


def list_milestones(repo: str, *, state: str = "all") -> list[dict[str, Any]]:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/milestones?" + urllib.parse.urlencode({"state": state, "per_page": 100})
    data = _request("GET", url, payload=None, timeout_sec=20)
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        out.append(
            {
                "number": int(raw.get("number") or 0),
                "title": str(raw.get("title") or "").strip(),
                "state": str(raw.get("state") or "").strip(),
                "description": str(raw.get("description") or "").strip(),
                "due_on": raw.get("due_on"),
            }
        )
    return [m for m in out if m["number"] > 0 and m["title"]]


def ensure_milestone(repo: str, *, title: str, description: str = "", state: str = "open", due_on: Optional[str] = None) -> int:
    wanted = _norm(title)
    if not wanted:
        return 0
    for ms in list_milestones(repo, state="all"):
        if str(ms.get("title") or "").strip() == wanted:
            return int(ms.get("number") or 0)
    owner, name = _parse_repo(repo)
    payload: dict[str, Any] = {"title": wanted, "state": state or "open"}
    if description:
        payload["description"] = description
    if due_on:
        payload["due_on"] = due_on
    data = _request("POST", _api_url() + f"/repos/{owner}/{name}/milestones", payload=payload, timeout_sec=30)
    return int(data.get("number") or 0)


def get_issue(repo: str, number: int) -> IssueRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/{int(number)}"
    data = _request("GET", url, payload=None, timeout_sec=20)
    labels = [str((x or {}).get("name") or "").strip() for x in (data.get("labels") or []) if str((x or {}).get("name") or "").strip()]
    return IssueRef(
        number=int(data.get("number") or number),
        url=str(data.get("html_url") or ""),
        title=str(data.get("title") or ""),
        body=str(data.get("body") or ""),
        state=str(data.get("state") or ""),
        labels=labels,
    )


def create_issue(repo: str, *, title: str, body: str, labels: Optional[list[str]] = None, milestone: Optional[int] = None) -> IssueRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues"
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        ensure_labels(repo, labels)
        payload["labels"] = [str(x).strip() for x in labels if str(x).strip()]
    if milestone:
        payload["milestone"] = int(milestone)
    data = _request("POST", url, payload=payload, timeout_sec=30)
    return get_issue(repo, int(data.get("number") or 0))


def update_issue(
    repo: str,
    number: int,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    labels: Optional[list[str]] = None,
    state: Optional[str] = None,
    milestone: Any = _UNSET,
) -> IssueRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/{int(number)}"
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if labels is not None:
        ensure_labels(repo, labels)
        payload["labels"] = [str(x).strip() for x in labels if str(x).strip()]
    if state is not None:
        payload["state"] = state
    if milestone is not _UNSET:
        payload["milestone"] = int(milestone) if milestone else None
    _request("PATCH", url, payload=payload, timeout_sec=30)
    return get_issue(repo, number)


def update_issue_body(repo: str, number: int, body: str) -> IssueRef:
    return update_issue(repo, number, body=body)


def ensure_issue(
    repo: str,
    *,
    title: str,
    body: str,
    allow_create: bool,
    labels: Optional[list[str]] = None,
    milestone: Optional[int] = None,
    marker: str = "",
) -> IssueRef:
    found = search_issue_by_marker(repo, marker) if _norm(marker) else search_issue_by_title(repo, title)
    if found:
        return found
    if not allow_create:
        raise GitHubIssuesBusError(f"issue not found and create is disabled: title={title}")
    return create_issue(repo, title=title, body=body, labels=labels, milestone=milestone)


@dataclass(frozen=True)
class CommentRef:
    id: int
    url: str
    body: str
    user_login: str = ""
    created_at: str = ""
    updated_at: str = ""


def list_issue_comments(repo: str, number: int, *, per_page: int = 100) -> list[CommentRef]:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/{int(number)}/comments?per_page={int(per_page)}"
    data = _request("GET", url, payload=None, timeout_sec=20)
    out: list[CommentRef] = []
    if isinstance(data, list):
        for it in data:
            user = it.get("user") or {}
            out.append(
                CommentRef(
                    id=int(it.get("id") or 0),
                    url=str(it.get("html_url") or ""),
                    body=str(it.get("body") or ""),
                    user_login=str(user.get("login") or ""),
                    created_at=str(it.get("created_at") or ""),
                    updated_at=str(it.get("updated_at") or ""),
                )
            )
    return [c for c in out if c.id > 0]


def create_issue_comment(repo: str, number: int, *, body: str) -> CommentRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/{int(number)}/comments"
    data = _request("POST", url, payload={"body": body}, timeout_sec=30)
    user = data.get("user") or {}
    return CommentRef(
        id=int(data.get("id") or 0),
        url=str(data.get("html_url") or ""),
        body=str(data.get("body") or ""),
        user_login=str(user.get("login") or ""),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
    )


def update_issue_comment(repo: str, comment_id: int, *, body: str) -> CommentRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/comments/{int(comment_id)}"
    data = _request("PATCH", url, payload={"body": body}, timeout_sec=30)
    user = data.get("user") or {}
    return CommentRef(
        id=int(data.get("id") or comment_id),
        url=str(data.get("html_url") or ""),
        body=str(data.get("body") or ""),
        user_login=str(user.get("login") or ""),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
    )


def upsert_comment_with_marker(repo: str, issue_number: int, *, marker: str, body: str, allow_create: bool) -> CommentRef:
    marker = marker.strip()
    comments = list_issue_comments(repo, issue_number)
    for c in comments:
        if marker in (c.body or ""):
            return update_issue_comment(repo, c.id, body=body)
    if not allow_create:
        raise GitHubIssuesBusError("comment not found and create is disabled")
    return create_issue_comment(repo, issue_number, body=body)
