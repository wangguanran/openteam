import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .github_projects_client import GitHubAuthError, resolve_github_token


class GitHubIssuesBusError(Exception):
    pass


def _norm(s: str) -> str:
    return (s or "").strip()


def _api_url() -> str:
    # REST API base
    return "https://api.github.com"


def _request(method: str, url: str, *, payload: Optional[dict[str, Any]] = None, timeout_sec: int = 20) -> dict[str, Any]:
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


@dataclass(frozen=True)
class IssueRef:
    number: int
    url: str
    title: str
    body: str


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


def get_issue(repo: str, number: int) -> IssueRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/{int(number)}"
    data = _request("GET", url, payload=None, timeout_sec=20)
    return IssueRef(number=int(data.get("number") or number), url=str(data.get("html_url") or ""), title=str(data.get("title") or ""), body=str(data.get("body") or ""))


def create_issue(repo: str, *, title: str, body: str) -> IssueRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues"
    data = _request("POST", url, payload={"title": title, "body": body}, timeout_sec=30)
    return IssueRef(number=int(data.get("number") or 0), url=str(data.get("html_url") or ""), title=title, body=body)


def update_issue_body(repo: str, number: int, body: str) -> IssueRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/{int(number)}"
    data = _request("PATCH", url, payload={"body": body}, timeout_sec=30)
    return IssueRef(number=int(data.get("number") or number), url=str(data.get("html_url") or ""), title=str(data.get("title") or ""), body=str(data.get("body") or ""))


def ensure_issue(repo: str, *, title: str, body: str, allow_create: bool) -> IssueRef:
    found = search_issue_by_title(repo, title)
    if found:
        return found
    if not allow_create:
        raise GitHubIssuesBusError(f"issue not found and create is disabled: title={title}")
    return create_issue(repo, title=title, body=body)


@dataclass(frozen=True)
class CommentRef:
    id: int
    url: str
    body: str


def list_issue_comments(repo: str, number: int, *, per_page: int = 100) -> list[CommentRef]:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/{int(number)}/comments?per_page={int(per_page)}"
    data = _request("GET", url, payload=None, timeout_sec=20)
    out: list[CommentRef] = []
    if isinstance(data, list):
        for it in data:
            out.append(CommentRef(id=int(it.get("id") or 0), url=str(it.get("html_url") or ""), body=str(it.get("body") or "")))
    return [c for c in out if c.id > 0]


def create_issue_comment(repo: str, number: int, *, body: str) -> CommentRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/{int(number)}/comments"
    data = _request("POST", url, payload={"body": body}, timeout_sec=30)
    return CommentRef(id=int(data.get("id") or 0), url=str(data.get("html_url") or ""), body=str(data.get("body") or ""))


def update_issue_comment(repo: str, comment_id: int, *, body: str) -> CommentRef:
    owner, name = _parse_repo(repo)
    url = _api_url() + f"/repos/{owner}/{name}/issues/comments/{int(comment_id)}"
    data = _request("PATCH", url, payload={"body": body}, timeout_sec=30)
    return CommentRef(id=int(data.get("id") or comment_id), url=str(data.get("html_url") or ""), body=str(data.get("body") or ""))


def upsert_comment_with_marker(repo: str, issue_number: int, *, marker: str, body: str, allow_create: bool) -> CommentRef:
    marker = marker.strip()
    comments = list_issue_comments(repo, issue_number)
    for c in comments:
        if marker in (c.body or ""):
            return update_issue_comment(repo, c.id, body=body)
    if not allow_create:
        raise GitHubIssuesBusError("comment not found and create is disabled")
    return create_issue_comment(repo, issue_number, body=body)

