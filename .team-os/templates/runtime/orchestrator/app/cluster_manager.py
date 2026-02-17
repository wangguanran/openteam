import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from .github_issues_bus import GitHubIssuesBusError, IssueRef, ensure_issue, get_issue, update_issue_body, upsert_comment_with_marker
from .github_projects_client import GitHubAuthError
from .state_store import team_os_root


class ClusterError(Exception):
    pass


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str):
    import datetime as _dt

    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _env_truthy(name: str, default: str = "0") -> bool:
    v = os.getenv(name, default).strip().lower()
    return v not in ("", "0", "false", "no", "off")


def load_cluster_config() -> dict[str, Any]:
    p = team_os_root() / ".team-os" / "cluster" / "config.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _cluster_section(cfg: dict[str, Any]) -> dict[str, Any]:
    c = cfg.get("cluster") or {}
    return c if isinstance(c, dict) else {}


def cluster_enabled(cfg: dict[str, Any]) -> bool:
    c = _cluster_section(cfg)
    return bool(c.get("enabled", False))


def _central_allowlist_path() -> Path:
    return team_os_root() / ".team-os" / "policies" / "central_model_allowlist.yaml"


def load_central_model_allowlist() -> list[str]:
    """
    Central Brain model allowlist (deterministic truth source).
    If missing/empty: treat as DENY (fail-safe) in cluster mode.
    """
    p = _central_allowlist_path()
    if not p.exists():
        return []
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    if not isinstance(d, dict):
        return []
    ids = d.get("allowed_model_ids") or []
    if not isinstance(ids, list):
        return []
    out: list[str] = []
    for x in ids:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return sorted(set(out))


def local_llm_profile() -> dict[str, str]:
    """
    Resolve local llm_profile deterministically from env vars.

    Required for leader qualification in cluster mode:
    - TEAMOS_LLM_MODEL_ID
    """
    provider = str(os.getenv("TEAMOS_LLM_PROVIDER") or "codex").strip() or "codex"
    model_id = str(os.getenv("TEAMOS_LLM_MODEL_ID") or "").strip()
    auth_mode = str(os.getenv("TEAMOS_LLM_AUTH_MODE") or "oauth").strip() or "oauth"
    return {"provider": provider, "model_id": model_id, "auth_mode": auth_mode}


def qualify_leader(*, allowlist: list[str], profile: dict[str, str]) -> dict[str, Any]:
    model_id = str(profile.get("model_id") or "").strip()
    if not allowlist:
        return {"qualified": False, "reason": "allowlist_missing_or_empty", "model_id": model_id, "allowed_model_ids": []}
    if not model_id:
        return {"qualified": False, "reason": "missing_model_id", "model_id": "", "allowed_model_ids": allowlist}
    if model_id not in set(allowlist):
        return {"qualified": False, "reason": "model_not_allowed", "model_id": model_id, "allowed_model_ids": allowlist}
    return {"qualified": True, "reason": "allowed", "model_id": model_id, "allowed_model_ids": allowlist}


def cluster_repo(cfg: dict[str, Any]) -> str:
    c = _cluster_section(cfg)
    return str(c.get("cluster_repo") or "").strip()


def _leader_issue_title(cfg: dict[str, Any]) -> str:
    c = _cluster_section(cfg)
    lease = (c.get("leader_lease") or {}) if isinstance(c.get("leader_lease"), dict) else {}
    return str(lease.get("issue_title") or "CLUSTER-LEADER").strip()


def _nodes_issue_title(cfg: dict[str, Any]) -> str:
    c = _cluster_section(cfg)
    nr = (c.get("nodes_registry") or {}) if isinstance(c.get("nodes_registry"), dict) else {}
    return str(nr.get("issue_title") or "CLUSTER-NODES").strip()


def _write_enabled(cfg: dict[str, Any], *, section_key: str, default_env: str) -> bool:
    c = _cluster_section(cfg)
    sec = (c.get(section_key) or {}) if isinstance(c.get(section_key), dict) else {}
    env_name = str(sec.get("write_enable_env") or default_env).strip() or default_env
    return _env_truthy(env_name, "0")


def _lease_params(cfg: dict[str, Any]) -> dict[str, int]:
    c = _cluster_section(cfg)
    lease = (c.get("leader_lease") or {}) if isinstance(c.get("leader_lease"), dict) else {}
    ttl = int(lease.get("lease_ttl_sec") or 60)
    grace = int(lease.get("grace_sec") or 30)
    return {"ttl": ttl, "grace": grace}


def _render_leader_body(*, instance_id: str, base_url: str, lease_expires_at: str, lease_version: int) -> str:
    data = {
        "leader_instance_id": instance_id,
        "leader_base_url": base_url,
        "lease_expires_at": lease_expires_at,
        "lease_version": int(lease_version),
        "last_updated_at": _utc_now_iso(),
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _parse_leader_body(body: str) -> dict[str, Any]:
    try:
        d = yaml.safe_load(body or "") or {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _lease_is_expired(lease_expires_at: str, *, grace_sec: int) -> bool:
    if not lease_expires_at:
        return True
    try:
        exp = _parse_iso(str(lease_expires_at))
        now = _parse_iso(_utc_now_iso())
        return now.timestamp() > (exp.timestamp() + float(grace_sec))
    except Exception:
        return True


@dataclass(frozen=True)
class LeaderInfo:
    leader_instance_id: str
    leader_base_url: str
    lease_expires_at: str
    lease_version: int
    backend: str
    issue_url: str


def read_leader(cfg: dict[str, Any]) -> Optional[LeaderInfo]:
    if not cluster_enabled(cfg):
        return None
    repo = cluster_repo(cfg)
    if not repo:
        return None
    title = _leader_issue_title(cfg)
    try:
        issue = ensure_issue(repo, title=title, body=_render_leader_body(instance_id="", base_url="", lease_expires_at="", lease_version=0), allow_create=False)
        d = _parse_leader_body(issue.body)
        return LeaderInfo(
            leader_instance_id=str(d.get("leader_instance_id") or ""),
            leader_base_url=str(d.get("leader_base_url") or ""),
            lease_expires_at=str(d.get("lease_expires_at") or ""),
            lease_version=int(d.get("lease_version") or 0),
            backend="github_issues",
            issue_url=str(issue.url or ""),
        )
    except Exception:
        return None


def attempt_elect(cfg: dict[str, Any], *, instance_id: str, base_url: str) -> dict[str, Any]:
    if not cluster_enabled(cfg):
        return {"success": True, "reason": "cluster disabled -> local leader", "leader": {"leader_instance_id": instance_id, "backend": "local"}}
    repo = cluster_repo(cfg)
    if not repo:
        return {"success": True, "reason": "cluster_repo missing -> local leader", "leader": {"leader_instance_id": instance_id, "backend": "local"}}

    # Central Brain model allowlist gate (fail-safe).
    allow = load_central_model_allowlist()
    prof = local_llm_profile()
    qual = qualify_leader(allowlist=allow, profile=prof)
    if not bool(qual.get("qualified")):
        cur = read_leader(cfg)
        return {
            "success": False,
            "reason": "leader_qualification_failed",
            "detail": {"qualification": qual, "llm_profile": prof, "policy_path": str(_central_allowlist_path())},
            "leader": (cur.__dict__ if cur else {"leader_instance_id": instance_id, "backend": "local"}),
        }

    allow_write = _write_enabled(cfg, section_key="leader_lease", default_env="TEAMOS_GH_CLUSTER_WRITE_ENABLED")
    if not allow_write:
        cur = read_leader(cfg)
        return {
            "success": False,
            "reason": "remote writes disabled (set TEAMOS_GH_CLUSTER_WRITE_ENABLED=1 to enable GitHub lease writes)",
            "leader": (cur.__dict__ if cur else {"leader_instance_id": instance_id, "backend": "local"}),
        }

    params = _lease_params(cfg)
    ttl = params["ttl"]
    grace = params["grace"]

    title = _leader_issue_title(cfg)
    issue = ensure_issue(repo, title=title, body=_render_leader_body(instance_id="", base_url="", lease_expires_at="", lease_version=0), allow_create=True)
    cur = _parse_leader_body(issue.body)
    cur_leader = str(cur.get("leader_instance_id") or "")
    cur_exp = str(cur.get("lease_expires_at") or "")
    cur_ver = int(cur.get("lease_version") or 0)

    if (cur_leader and cur_leader != instance_id) and (not _lease_is_expired(cur_exp, grace_sec=grace)):
        return {"success": False, "reason": "lease held by other leader", "leader": {**cur, "backend": "github_issues", "issue_url": issue.url}}

    # Acquire/update lease
    import datetime as _dt

    exp = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=int(ttl))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    new_body = _render_leader_body(instance_id=instance_id, base_url=base_url, lease_expires_at=exp, lease_version=max(1, cur_ver + 1))
    _ = update_issue_body(repo, issue.number, new_body)
    updated = get_issue(repo, issue.number)
    d2 = _parse_leader_body(updated.body)
    ok = str(d2.get("leader_instance_id") or "") == instance_id
    return {"success": bool(ok), "reason": "elected" if ok else "write_conflict", "leader": {**d2, "backend": "github_issues", "issue_url": updated.url}}


def upsert_node_registry_comment(
    cfg: dict[str, Any],
    *,
    instance_id: str,
    body_yaml: str,
) -> dict[str, Any]:
    """
    Upsert the node's registry comment in CLUSTER-NODES issue.
    """
    repo = cluster_repo(cfg)
    if not repo:
        raise ClusterError("cluster_repo missing")
    allow_write = _write_enabled(cfg, section_key="nodes_registry", default_env="TEAMOS_GH_CLUSTER_WRITE_ENABLED")
    if not allow_write:
        raise ClusterError("remote writes disabled for nodes registry")

    issue = ensure_issue(repo, title=_nodes_issue_title(cfg), body="# CLUSTER-NODES\n", allow_create=True)
    marker = f"<!-- TEAMOS_NODE:{instance_id} -->"
    body = "\n".join([marker, "", body_yaml.strip(), ""]).strip() + "\n"
    c = upsert_comment_with_marker(repo, issue.number, marker=marker, body=body, allow_create=True)
    return {"issue_url": issue.url, "comment_url": c.url}
