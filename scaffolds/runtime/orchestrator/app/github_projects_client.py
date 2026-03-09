import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


class GitHubAuthError(Exception):
    pass


class GitHubAPIError(Exception):
    pass


@dataclass(frozen=True)
class RateLimit:
    remaining: int
    used: int
    reset_at: str
    cost: int


def _run(cmd: list[str], *, timeout_sec: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_sec, check=False)


def _which(cmd: str) -> Optional[str]:
    try:
        import shutil

        return shutil.which(cmd)
    except Exception:
        return None


def resolve_github_token() -> str:
    """
    Prefer GitHub CLI OAuth when available; fall back to env token.

    Env vars accepted:
    - GITHUB_TOKEN
    - GH_TOKEN
    """
    if _which("gh"):
        try:
            p = _run(["gh", "auth", "token", "-h", "github.com"], timeout_sec=10)
            out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
            if p.returncode == 0 and out:
                return out
        except Exception:
            pass

    tok = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if tok:
        return tok

    raise GitHubAuthError("Missing GitHub auth. Provide env GITHUB_TOKEN (recommended: from `gh auth token`) or login with gh.")


class GitHubGraphQL:
    def __init__(self, *, token: str, api_url: str = "https://api.github.com/graphql"):
        self.token = token
        self.api_url = api_url

    def graphql(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
        *,
        timeout_sec: int = 20,
        max_retries: int = 3,
        backoff_sec: float = 0.6,
    ) -> dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            method="POST",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "teamos-control-plane",
            },
        )

        # GitHub GraphQL occasionally fails with transient TLS EOF/connection resets.
        # Retry those network-layer failures with a small exponential backoff to make sync stable.
        body = ""
        for attempt in range(max(0, int(max_retries)) + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                # Retry only on transient upstream errors.
                if e.code in (500, 502, 503, 504) and attempt < max_retries:
                    time.sleep(backoff_sec * (2**attempt))
                    continue
                raise GitHubAPIError(f"GitHub GraphQL HTTP {e.code}: {body[:2000]}") from e
            except urllib.error.URLError as e:
                if attempt < max_retries:
                    time.sleep(backoff_sec * (2**attempt))
                    continue
                raise GitHubAPIError(f"GitHub GraphQL request failed: {e}") from e

        try:
            out = json.loads(body) if body else {}
        except Exception as e:
            raise GitHubAPIError(f"GitHub GraphQL invalid JSON: {body[:2000]}") from e

        if out.get("errors"):
            msg = json.dumps(out.get("errors"), ensure_ascii=False)[:2000]
            raise GitHubAPIError(f"GitHub GraphQL errors: {msg}")
        return out.get("data") or {}


PROJECT_QUERY_ORG_BY_NUMBER = """
query($owner:String!, $number:Int!) {
  organization(login: $owner) {
    projectV2(number: $number) { id number title url }
  }
}
"""


PROJECT_QUERY_USER_BY_NUMBER = """
query($owner:String!, $number:Int!) {
  user(login: $owner) {
    projectV2(number: $number) { id number title url }
  }
}
"""


PROJECT_QUERY_REPO_BY_NUMBER = """
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner: $owner, name: $repo) {
    projectV2(number: $number) { id number title url }
  }
}
"""


RATE_LIMIT_QUERY = """
query {
  rateLimit {
    limit
    cost
    remaining
    resetAt
  }
}
"""


PROJECT_FIELDS_QUERY = """
query($projectId: ID!) {
  node(id: $projectId) {
    ... on ProjectV2 {
      id
      number
      title
      url
      fields(first: 100) {
        nodes {
          __typename
          ... on ProjectV2Field {
            id
            name
            dataType
          }
          ... on ProjectV2SingleSelectField {
            id
            name
            dataType
            options { id name color description }
          }
          ... on ProjectV2IterationField {
            id
            name
            dataType
          }
        }
      }
    }
  }
}
"""


PROJECT_ITEMS_QUERY = """
query($projectId: ID!, $after: String) {
  node(id: $projectId) {
    ... on ProjectV2 {
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          type
          content {
            __typename
            ... on DraftIssue { id title body }
            ... on Issue { id title url number }
          }
          fieldValues(first: 50) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldTextValue {
                text
                field { ... on ProjectV2Field { id name } ... on ProjectV2SingleSelectField { id name } ... on ProjectV2IterationField { id name } }
              }
              ... on ProjectV2ItemFieldNumberValue {
                number
                field { ... on ProjectV2Field { id name } ... on ProjectV2SingleSelectField { id name } ... on ProjectV2IterationField { id name } }
              }
              ... on ProjectV2ItemFieldDateValue {
                date
                field { ... on ProjectV2Field { id name } ... on ProjectV2SingleSelectField { id name } ... on ProjectV2IterationField { id name } }
              }
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                optionId
                field { ... on ProjectV2Field { id name } ... on ProjectV2SingleSelectField { id name } ... on ProjectV2IterationField { id name } }
              }
              ... on ProjectV2ItemFieldIterationValue {
                title
                iterationId
                field { ... on ProjectV2Field { id name } ... on ProjectV2SingleSelectField { id name } ... on ProjectV2IterationField { id name } }
              }
            }
          }
        }
      }
    }
  }
}
"""


CREATE_FIELD_MUTATION = """
mutation($projectId: ID!, $name: String!, $dataType: ProjectV2CustomFieldType!, $singleSelectOptions: [ProjectV2SingleSelectFieldOptionInput!]) {
  createProjectV2Field(input: { projectId: $projectId, name: $name, dataType: $dataType, singleSelectOptions: $singleSelectOptions }) {
    projectV2Field {
      __typename
      ... on ProjectV2Field { id name dataType }
      ... on ProjectV2SingleSelectField { id name dataType options { id name color description } }
      ... on ProjectV2IterationField { id name dataType }
    }
  }
}
"""


UPDATE_ITEM_FIELD_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
  updateProjectV2ItemFieldValue(input: { projectId: $projectId, itemId: $itemId, fieldId: $fieldId, value: $value }) {
    projectV2Item { id }
  }
}
"""


ADD_DRAFT_ISSUE_MUTATION = """
mutation($projectId: ID!, $title: String!, $body: String) {
  addProjectV2DraftIssue(input: { projectId: $projectId, title: $title, body: $body }) {
    projectItem { id }
  }
}
"""


UPDATE_DRAFT_ISSUE_MUTATION = """
mutation($draftIssueId: ID!, $title: String, $body: String) {
  updateProjectV2DraftIssue(input: { draftIssueId: $draftIssueId, title: $title, body: $body }) {
    draftIssue { id title body }
  }
}
"""


def pick_project_from_number_query(data: dict[str, Any], owner_type: str) -> Optional[dict[str, Any]]:
    owner_type = (owner_type or "").strip().upper()
    if owner_type == "ORG":
        return (data.get("organization") or {}).get("projectV2")
    if owner_type == "USER":
        return (data.get("user") or {}).get("projectV2")
    if owner_type == "REPO":
        return (data.get("repository") or {}).get("projectV2")
    # Fallback: prefer org > user > repo.
    return (data.get("organization") or {}).get("projectV2") or (data.get("user") or {}).get("projectV2") or (data.get("repository") or {}).get("projectV2")
