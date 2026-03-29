import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import yaml

from . import improvement_store, workspace_store
from .github_projects_client import (
    ADD_DRAFT_ISSUE_MUTATION,
    CREATE_FIELD_MUTATION,
    PROJECT_FIELDS_QUERY,
    PROJECT_ITEMS_QUERY,
    PROJECT_QUERY_ORG_BY_NUMBER,
    PROJECT_QUERY_REPO_BY_NUMBER,
    PROJECT_QUERY_USER_BY_NUMBER,
    UPDATE_ITEM_FIELD_MUTATION,
    UPDATE_DRAFT_ISSUE_MUTATION,
    GitHubAPIError,
    GitHubAuthError,
    GitHubGraphQL,
    pick_project_from_number_query,
    resolve_github_token,
)
from .panel_mapping import MappingDoc, PanelMappingError, get_project_cfg, load_mapping
from .plan_store import list_milestones
from .runtime_db import RuntimeDB
from .state_store import ledger_tasks_dir, load_focus, runtime_state_root, openteam_root, openteam_requirements_dir
from .workspace_store import ensure_project_scaffold, ledger_tasks_dir as ws_ledger_tasks_dir, requirements_dir as ws_requirements_dir


class PanelSyncError(Exception):
    pass


@dataclass(frozen=True)
class DesiredItem:
    key: str
    kind: str
    title: str
    body: str
    field_values: dict[str, Any]


def _norm(s: str) -> str:
    return (s or "").strip()


def _lower(s: str) -> str:
    return _norm(s).lower()


def _parse_date_from_iso(ts: str) -> str:
    ts = _norm(ts)
    if not ts:
        return ""
    # Accept either "YYYY-MM-DD" or ISO-8601 "YYYY-MM-DDTHH:MM:SSZ".
    if "T" in ts:
        return ts.split("T", 1)[0]
    if len(ts) >= 10:
        return ts[:10]
    return ""


def _issue_style_type(kind: str = "", lane: str = "") -> str:
    raw = _lower(lane or kind)
    if raw in ("bug", "ci", "regression"):
        return "Bug"
    if raw in ("quality", "code_quality", "cleanup", "refactor"):
        return "Quality"
    if raw in ("process", "ops", "maintenance"):
        return "Process"
    return "Feature"


def _milestone_status_key(state: str) -> str:
    raw = _lower(state)
    if raw == "released":
        return "DONE"
    if raw in ("release-candidate", "frozen"):
        return "IN_REVIEW"
    if raw == "blocked":
        return "BLOCKED"
    if raw == "active":
        return "IN_PROGRESS"
    return "TODO"


def _panel_item_title(raw_title: str, *, kind: str = "", lane: str = "", module: str = "") -> str:
    title = _norm(raw_title)
    if not title:
        return "[Feature][General] 未命名事项"
    if re.match(r"^\[(Bug|Feature|Process|Quality)\]\[[^\]]+\]\s+\S+", title):
        return title
    mod = _norm(module) or "General"
    return f"[{_issue_style_type(kind=kind, lane=lane)}][{mod}] {title}"


def _load_tasks(project_id: str) -> list[dict[str, Any]]:
    if str(project_id) == "openteam":
        d = ledger_tasks_dir()
    else:
        ensure_project_scaffold(project_id)
        d = ws_ledger_tasks_dir(project_id)
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        pid = str(data.get("project_id") or "").strip()
        if pid != project_id:
            continue
        data["_path"] = str(p)
        out.append(data)
    return out


def _load_requirements_need_pm(project_id: str) -> list[dict[str, Any]]:
    if str(project_id) == "openteam":
        req_dir = openteam_requirements_dir()
    else:
        ensure_project_scaffold(project_id)
        req_dir = ws_requirements_dir(project_id)
    y = req_dir / "requirements.yaml"
    if not y.exists():
        return []
    data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
    out: list[dict[str, Any]] = []
    for r in (data.get("requirements") or []):
        if str(r.get("status") or "").upper() == "NEED_PM_DECISION":
            out.append(r)
    return out


def _load_requirements(project_id: str) -> list[dict[str, Any]]:
    if str(project_id) == "openteam":
        req_dir = openteam_requirements_dir()
    else:
        ensure_project_scaffold(project_id)
        req_dir = ws_requirements_dir(project_id)
    y = req_dir / "requirements.yaml"
    if not y.exists():
        return []
    data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
    return list(data.get("requirements") or [])


def _load_team_feature_proposals(project_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc in improvement_store.list_proposals(project_id=str(project_id or "")):
        if not isinstance(doc, dict):
            continue
        lane = str(doc.get("lane") or "").strip().lower()
        if lane not in ("feature", "process", "quality"):
            continue
        status = str(doc.get("status") or "").strip().upper()
        if status in ("REJECTED", "MATERIALIZED"):
            continue
        out.append({"proposal_id": str(doc.get("proposal_id") or ""), **doc})
    return sorted(out, key=lambda x: (str(x.get("updated_at") or ""), str(x.get("proposal_id") or "")), reverse=True)


def _join_links(links: Any) -> str:
    if isinstance(links, dict):
        parts = []
        for k, v in links.items():
            v = str(v or "").strip()
            if not v:
                continue
            parts.append(f"{k}={v}")
        return "\n".join(parts)
    if isinstance(links, list):
        return "\n".join([str(x) for x in links if str(x).strip()])
    return str(links or "").strip()


def _desired_items(
    *,
    project_id: str,
    mapping: MappingDoc,
    db: RuntimeDB,
) -> list[DesiredItem]:
    cfg = get_project_cfg(mapping, project_id) or {}
    focus = load_focus()
    focus_obj = str(focus.get("objective") or "").strip()

    # Agents by task
    agents = [a for a in db.list_agents(project_id=project_id)]
    by_task: dict[str, list[Any]] = {}
    for a in agents:
        by_task.setdefault(a.task_id or "", []).append(a)

    def status_key_from_ledger(state: str) -> str:
        sm = cfg.get("status_mapping") or {}
        k = sm.get(_lower(state))
        return str(k or "TODO")

    def risk_key_from_ledger(risk: str) -> str:
        rm = cfg.get("risk_mapping") or {}
        k = rm.get(str(risk or "").strip().upper())
        return str(k or "LOW")

    def option_name(field_key: str, option_key: str, fallback: str) -> str:
        fields = cfg.get("fields") or {}
        field_cfg = fields.get(field_key) or {}
        options = field_cfg.get("options") or {}
        opt = options.get(option_key) or {}
        return str(opt.get("name") or fallback)

    items: list[DesiredItem] = []

    # Tasks
    for t in _load_tasks(project_id):
        tid = str(t.get("id") or "").strip()
        team_workflow_doc = t.get("team_workflow")
        if not isinstance(team_workflow_doc, dict):
            team_workflow_doc = {}
        title = _panel_item_title(
            str(t.get("title") or "").strip(),
            kind=str((team_workflow_doc or {}).get("kind") or ""),
            lane=str((team_workflow_doc or {}).get("lane") or ""),
            module=str((team_workflow_doc or {}).get("module") or (((t.get("execution_policy") or {}) if isinstance(t.get("execution_policy"), dict) else {}).get("module")) or ""),
        )
        state = str(t.get("status") or t.get("state") or "").strip()
        wsid = str(t.get("workstream_id") or "general").strip()
        risk = str(t.get("risk_level") or t.get("risk") or "").strip()
        need_pm = bool(t.get("need_pm_decision") or False)
        links_text = _join_links(t.get("links") or {})
        repo_info = t.get("repo") or {}
        if not isinstance(repo_info, dict):
            repo_info = {}
        repo_locator = str(repo_info.get("locator") or "").strip()
        repo_mode = str(repo_info.get("mode") or "").strip()

        assigned = by_task.get(tid, [])
        last_hb = ""
        if assigned:
            last_hb = max([_norm(a.last_heartbeat) for a in assigned if _norm(a.last_heartbeat)], default="")

        items.append(
            DesiredItem(
                key=tid,
                kind="TASK",
                title=title,
                body="\n".join(
                    [
                        f"任务 ID: {tid}",
                        f"台账: {t.get('artifacts', {}).get('ledger', t.get('_path', ''))}",
                        f"日志: {t.get('artifacts', {}).get('logs_dir', '')}",
                        "",
                        "关联信息:",
                        links_text or "(none)",
                    ]
                ).strip()
                + "\n",
                field_values={
                    "task_id": tid,
                    "openteam_status": option_name("openteam_status", status_key_from_ledger(state), status_key_from_ledger(state)),
                    "workstreams": wsid,
                    "risk": option_name("risk", risk_key_from_ledger(risk), risk_key_from_ledger(risk)),
                    "need_pm_decision": option_name("need_pm_decision", "YES" if need_pm else "NO", "Yes" if need_pm else "No"),
                    "current_focus": focus_obj,
                    "active_agents": len(assigned),
                    "last_heartbeat": last_hb,
                    "start_date": _parse_date_from_iso(str(t.get("start_date") or "")),
                    "target_date": _parse_date_from_iso(str(t.get("target_date") or "")),
                    "links": links_text,
                    "repo_locator": repo_locator,
                    "repo_mode": repo_mode,
                },
            )
        )

    # Requirements (Backlog view): sync ACTIVE/DEPRECATED/CONFLICT as draft items.
    # NOTE: NEED_PM_DECISION requirements are represented as DECISION items below for visibility.
    for r in _load_requirements(project_id):
        rid = str(r.get("req_id") or "").strip()
        if not rid:
            continue
        st = str(r.get("status") or "ACTIVE").strip().upper()
        if st == "NEED_PM_DECISION":
            continue
        ws = list(r.get("workstreams") or []) or ["general"]
        status_key = "TODO"
        if st in ("DEPRECATED", "DONE", "CLOSED"):
            status_key = "DONE"
        elif st in ("CONFLICT",):
            status_key = "BLOCKED"
        items.append(
            DesiredItem(
                key=f"REQ:{rid}",
                kind="REQ",
                title=f"[REQ] {rid} {str(r.get('title') or '').strip()}".strip(),
                body="\n".join(
                    [
                        f"Requirement ID: {rid}",
                        f"Status: {st}",
                        f"Priority: {r.get('priority','')}",
                        "",
                        "Text:",
                        str(r.get("text") or "").strip(),
                        "",
                        "Refs:",
                        "\n".join([str(x) for x in (r.get("decision_log_refs") or []) if str(x).strip()]) or "(none)",
                    ]
                ).strip()
                + "\n",
                field_values={
                    "task_id": f"REQ:{rid}",
                    "openteam_status": option_name("openteam_status", status_key, status_key),
                    "workstreams": ",".join(str(x) for x in ws),
                    "risk": option_name("risk", "LOW", "LOW"),
                    "need_pm_decision": option_name("need_pm_decision", "YES" if st in ("CONFLICT",) else "NO", "Yes" if st in ("CONFLICT",) else "No"),
                    "current_focus": focus_obj,
                    "active_agents": 0,
                    "last_heartbeat": "",
                    "start_date": "",
                    "target_date": "",
                    "links": "",
                    "repo_locator": "",
                    "repo_mode": "",
                },
            )
        )

    # Decisions from requirements NEED_PM_DECISION
    for r in _load_requirements_need_pm(project_id):
        rid = str(r.get("req_id") or "").strip()
        title = str(r.get("title") or "").strip()
        ws = list(r.get("workstreams") or []) or ["general"]
        refs = list(r.get("decision_log_refs") or [])
        links_text = "\n".join([str(x) for x in refs if str(x).strip()])
        items.append(
            DesiredItem(
                key=f"DECISION:{rid}",
                kind="DECISION",
                title=f"[DECISION] {rid} {title}".strip(),
                body="\n".join(
                    [
                        f"Decision for requirement: {rid}",
                        "",
                        "Original text:",
                        str(r.get("text") or "").strip(),
                        "",
                        "Conflict/Decision refs:",
                        links_text or "(none)",
                    ]
                ).strip()
                + "\n",
                field_values={
                    "task_id": f"DECISION:{rid}",
                    "openteam_status": option_name("openteam_status", "BLOCKED", "BLOCKED"),
                    "workstreams": ",".join(str(x) for x in ws),
                    "risk": option_name("risk", "MED", "MED"),
                    "need_pm_decision": option_name("need_pm_decision", "YES", "Yes"),
                    "current_focus": focus_obj,
                    "active_agents": 0,
                    "last_heartbeat": "",
                    "start_date": "",
                    "target_date": "",
                    "links": links_text,
                    "repo_locator": "",
                    "repo_mode": "",
                },
            )
        )

    # Self-upgrade proposals waiting for discussion / confirmation.
    for p in _load_team_feature_proposals(project_id):
        proposal_id = str(p.get("proposal_id") or "").strip()
        status = str(p.get("status") or "").strip().upper()
        panel_title = _panel_item_title(
            str(p.get("discussion_issue_title") or p.get("title") or "").strip(),
            kind=str(p.get("kind") or ""),
            lane=str(p.get("lane") or "feature"),
            module=str(p.get("module") or ""),
        )
        links_text = "\n".join(
            [
                f"discussion_issue={str(p.get('discussion_issue_url') or '').strip()}",
                f"repo={str(p.get('repo_locator') or '').strip()}",
            ]
        ).strip()
        status_key = "TODO" if status == "APPROVED" else "BLOCKED"
        items.append(
            DesiredItem(
                key=f"FEATURE_PROPOSAL:{proposal_id}",
                kind="DECISION",
                title=panel_title,
                body="\n".join(
                    [
                        f"提案 ID: {proposal_id}",
                        f"状态: {status}",
                        f"目标版本: {str(p.get('target_version') or '').strip()}",
                        f"冷静期截止: {str(p.get('cooldown_until') or '').strip()}",
                        "",
                        "概要:",
                        str(p.get("summary") or "").strip() or "(none)",
                        "",
                        "讨论 issue:",
                        str(p.get("discussion_issue_url") or "").strip() or "(missing)",
                    ]
                ).strip()
                + "\n",
                field_values={
                    "task_id": f"FEATURE_PROPOSAL:{proposal_id}",
                    "openteam_status": option_name("openteam_status", status_key, status_key),
                    "workstreams": str(p.get("workstream_id") or "general").strip() or "general",
                    "risk": option_name("risk", "MED", "MED"),
                    "need_pm_decision": option_name("need_pm_decision", "YES", "Yes"),
                    "current_focus": focus_obj,
                    "active_agents": 0,
                    "last_heartbeat": str(p.get("discussion_reply_updated_at") or p.get("updated_at") or ""),
                    "start_date": "",
                    "target_date": _parse_date_from_iso(str(p.get("cooldown_until") or "")),
                    "links": links_text,
                    "repo_locator": str(p.get("repo_locator") or "").strip(),
                    "repo_mode": "proposal",
                },
            )
        )

    # Milestones from plan overlay
    for m in list_milestones(project_id):
        links_text = _join_links(m.links)
        panel_title = _panel_item_title(f"跟踪 {m.title} 版本发布", kind="PROCESS", lane="process", module="Release")
        items.append(
            DesiredItem(
                key=f"MILESTONE:{m.milestone_id}",
                kind="MILESTONE",
                title=panel_title,
                body="\n".join(
                    [
                        f"Milestone: {m.milestone_id}",
                        f"状态: {m.state}",
                        f"发布线: {m.release_line or '(none)'}",
                        f"目标版本: {m.target_version or '(none)'}",
                        f"统计: total={m.total_items} open={m.open_items} blocked={m.blocked_items} done={m.done_items}",
                        "",
                        m.objective or "",
                        "",
                        (
                            f"Plan: docs/plans/openteam/plan.yaml"
                            if str(project_id) == "openteam"
                            else f"Plan: <WORKSPACE>/projects/{project_id}/state/plan/plan.yaml"
                        ),
                        "",
                        "Links:",
                        links_text or "(none)",
                    ]
                ).strip()
                + "\n",
                field_values={
                    "task_id": f"MILESTONE:{m.milestone_id}",
                    "openteam_status": option_name("openteam_status", _milestone_status_key(m.state), _milestone_status_key(m.state)),
                    "workstreams": ",".join(str(x) for x in (m.workstreams or [])) or "general",
                    "risk": option_name("risk", "LOW", "LOW"),
                    "need_pm_decision": option_name("need_pm_decision", "NO", "No"),
                    "current_focus": focus_obj,
                    "active_agents": 0,
                    "last_heartbeat": "",
                    "start_date": _parse_date_from_iso(m.start_date),
                    "target_date": _parse_date_from_iso(m.target_date),
                    "links": links_text,
                    "repo_locator": "",
                    "repo_mode": "",
                },
            )
        )

    items.extend(_delivery_request_items(project_id=project_id, mapping=mapping, db=db))

    return items


def _delivery_request_items(*, project_id: str, mapping: MappingDoc, db: RuntimeDB) -> list[DesiredItem]:
    _ = mapping, db
    items: list[DesiredItem] = []
    for request_path in sorted(workspace_store.delivery_requests_dir(project_id).glob("*/request.yaml")):
        doc = yaml.safe_load(request_path.read_text(encoding="utf-8")) or {}
        request_id = str(doc.get("request_id") or "").strip()
        if not request_id:
            continue
        items.append(
            DesiredItem(
                key=request_id,
                kind="REQUEST",
                title=f"[REQ] {request_id} {str(doc.get('title') or '').strip()}".strip(),
                body="\n".join(
                    [
                        f"Request ID: {request_id}",
                        f"Project: {doc.get('project_id','')}",
                        "",
                        str(doc.get("text") or "").strip(),
                    ]
                ).strip()
                + "\n",
                field_values={
                    "request_id": request_id,
                    "project_name": str(doc.get("project_id") or ""),
                    "priority": str(doc.get("priority") or "P1"),
                    "stage": str(doc.get("stage") or "Discussing"),
                    "spec_approved": "Yes" if bool(doc.get("spec_approved")) else "No",
                    "change_request": str(doc.get("change_request_of") or ""),
                    "review_gate": str(doc.get("review_gate") or "Pending"),
                    "ci": str(doc.get("ci") or "Pending"),
                    "release_ready": "Yes" if bool(doc.get("release_ready")) else "No",
                    "owner": str(doc.get("owner") or ""),
                    "blocked_reason": str(doc.get("blocked_reason") or ""),
                    "needs_you": "Yes" if bool(doc.get("needs_you")) else "No",
                    "pr": str(doc.get("pr") or ""),
                },
            )
        )
    return items


def _required_field_specs(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    # Normalize mapping.yaml "fields" section into a list of desired specs.
    fields = cfg.get("fields") or {}
    out: list[dict[str, Any]] = []
    if not isinstance(fields, dict):
        return out
    for key, f in fields.items():
        if not isinstance(f, dict):
            continue
        out.append(
            {
                "key": str(key),
                "name": str(f.get("name") or "").strip(),
                "type": str(f.get("type") or "").strip().upper(),
                "field_id": str(f.get("field_id") or "").strip(),
                "options": f.get("options") or {},
            }
        )
    return [x for x in out if x["name"] and x["type"]]


def _field_value_input(field_type: str, *, text: str = "", number: Optional[float] = None, date: str = "", single_select_option_id: str = "") -> dict[str, Any]:
    ft = (field_type or "").upper()
    if ft == "TEXT":
        return {"text": text}
    if ft == "NUMBER":
        return {"number": float(number or 0.0)}
    if ft == "DATE":
        # GitHub expects Date scalar "YYYY-MM-DD"
        return {"date": date or None}
    if ft == "SINGLE_SELECT":
        return {"singleSelectOptionId": single_select_option_id}
    raise PanelSyncError(f"unsupported field type: {field_type}")


def _parse_project_fields(field_nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Return: by_name[name] -> {id,name,dataType,options_by_name}
    """
    out: dict[str, dict[str, Any]] = {}
    for n in field_nodes or []:
        name = str(n.get("name") or "").strip()
        if not name:
            continue
        dt = str(n.get("dataType") or "").strip()
        fid = str(n.get("id") or "").strip()
        opts_by_name: dict[str, dict[str, Any]] = {}
        if n.get("__typename") == "ProjectV2SingleSelectField":
            for o in (n.get("options") or []):
                oname = str(o.get("name") or "").strip()
                if not oname:
                    continue
                opts_by_name[oname] = {"id": str(o.get("id") or ""), "name": oname}
        out[name] = {"id": fid, "name": name, "dataType": dt, "options_by_name": opts_by_name, "raw": n}
    return out


def _make_single_select_options(spec: dict[str, Any]) -> list[dict[str, Any]]:
    opts = spec.get("options") or {}
    out: list[dict[str, Any]] = []
    if not isinstance(opts, dict):
        return out
    for _k, v in opts.items():
        if not isinstance(v, dict):
            continue
        name = str(v.get("name") or "").strip()
        if not name:
            continue
        out.append({"name": name, "color": "GRAY", "description": ""})
    return out


class GitHubProjectsPanelSync:
    """
    Sync OpenTeam truth -> GitHub Projects v2 (view layer).

    Safety:
    - dry_run: does not call GitHub, only computes planned actions.
    - incremental/full: performs remote changes (project items/fields) and must be explicitly called/enabled.
    """

    def __init__(self, *, db: RuntimeDB):
        self.db = db

    def sync(self, *, project_id: str, mode: str, dry_run: bool) -> dict[str, Any]:
        mode = (mode or "incremental").strip().lower()
        if mode not in ("incremental", "full"):
            raise PanelSyncError(f"invalid mode={mode}; expected incremental|full")

        try:
            mapping = load_mapping()
        except PanelMappingError as e:
            # Still allow dry-run without mapping file by returning a minimal plan.
            if dry_run:
                mapping = MappingDoc(path=openteam_root() / "integrations" / "github_projects" / "mapping.yaml", sha256="missing", data={"projects": {}})
            else:
                raise

        cfg = get_project_cfg(mapping, project_id) or {}
        desired = _desired_items(project_id=project_id, mapping=mapping, db=self.db)

        if dry_run:
            actions = []
            for it in desired:
                actions.append(
                    {
                        "action": "WOULD_CREATE_OR_UPDATE",
                        "key": it.key,
                        "kind": it.kind,
                        "title": it.title,
                        "field_values": dict(it.field_values),
                    }
                )
            return {
                "project_id": project_id,
                "mode": mode,
                "dry_run": True,
                "mapping_sha256": mapping.sha256,
                "stats": {"created": len(desired), "updated": 0, "skipped": 0, "errors": 0},
                "actions": actions,
                "errors": [],
                "note": "dry-run does not call GitHub; all items are treated as create/update candidates.",
            }

        # --- Real sync below ---
        owner_type = str(cfg.get("owner_type") or "").strip().upper()
        owner = str(cfg.get("owner") or "").strip()
        repo = str(cfg.get("repo") or "").strip()
        project_number = int(cfg.get("project_number") or 0)
        project_node_id = str(cfg.get("project_node_id") or "").strip()

        if not project_node_id and (not owner or project_number <= 0):
            raise PanelSyncError("GitHub project binding is missing. Fill mapping.yaml: owner + project_number (or project_node_id).")
        if not project_node_id and owner_type == "REPO" and (not repo):
            raise PanelSyncError("GitHub project binding is missing repo name for owner_type=REPO (mapping.yaml: repo).")

        token = resolve_github_token()
        api_url = str((mapping.data.get("github") or {}).get("graphql_api_url") or "https://api.github.com/graphql").strip()
        gh = GitHubGraphQL(token=token, api_url=api_url)

        # Resolve project node id
        project_url = str(cfg.get("project_url") or "").strip()
        if not project_node_id:
            if owner_type == "ORG":
                data = gh.graphql(PROJECT_QUERY_ORG_BY_NUMBER, {"owner": owner, "number": project_number})
            elif owner_type == "USER":
                data = gh.graphql(PROJECT_QUERY_USER_BY_NUMBER, {"owner": owner, "number": project_number})
            elif owner_type == "REPO":
                data = gh.graphql(PROJECT_QUERY_REPO_BY_NUMBER, {"owner": owner, "repo": repo, "number": project_number})
            else:
                raise PanelSyncError(f"invalid owner_type={owner_type}; expected ORG|USER|REPO")
            p = pick_project_from_number_query(data, owner_type)
            if not p or not p.get("id"):
                raise PanelSyncError(f"GitHub project not found: owner_type={owner_type} owner={owner} number={project_number}")
            project_node_id = str(p["id"])
            project_url = str(p.get("url") or project_url)

        # Fetch fields
        pdata = gh.graphql(PROJECT_FIELDS_QUERY, {"projectId": project_node_id})
        node = (pdata.get("node") or {})
        field_nodes = (((node.get("fields") or {}).get("nodes")) or [])
        by_name = _parse_project_fields(field_nodes)

        # Ensure required custom fields exist (create if missing).
        field_specs = _required_field_specs(cfg)
        resolved_fields: dict[str, dict[str, Any]] = {}
        create_errors: list[str] = []
        for spec in field_specs:
            key = spec["key"]
            name = spec["name"]
            ftype = spec["type"]
            fid = spec["field_id"]

            existing = by_name.get(name)
            if existing and (not fid):
                fid = str(existing.get("id") or "").strip()
            if not fid and mode == "full":
                single_opts = _make_single_select_options(spec) if ftype == "SINGLE_SELECT" else None
                try:
                    c = gh.graphql(
                        CREATE_FIELD_MUTATION,
                        {"projectId": project_node_id, "name": name, "dataType": ftype, "singleSelectOptions": single_opts},
                    )
                    cfg_node = (((c.get("createProjectV2Field") or {}).get("projectV2Field")) or {})
                    fid = str(cfg_node.get("id") or "").strip()
                    # Refresh field_nodes for option ids after creation.
                    by_name[name] = {
                        "id": fid,
                        "name": name,
                        "dataType": str(cfg_node.get("dataType") or ""),
                        "options_by_name": {str(o.get("name") or ""): {"id": str(o.get("id") or ""), "name": str(o.get("name") or "")} for o in (cfg_node.get("options") or [])},
                        "raw": cfg_node,
                    }
                except GitHubAPIError as e:
                    create_errors.append(f"create_field_failed name={name}: {e}")

            if not fid:
                create_errors.append(f"missing_field name={name} key={key}")
                continue

            resolved_fields[key] = {"id": fid, "name": name, "type": ftype, "options_by_name": (by_name.get(name) or {}).get("options_by_name") or {}}

        if create_errors:
            raise PanelSyncError("Field mapping incomplete: " + "; ".join(create_errors[:5]))

        # Fetch existing items
        existing_by_key: dict[str, dict[str, Any]] = {}
        key_field_ids = {str(meta.get("id") or "").strip() for key, meta in resolved_fields.items() if key.endswith("_id")}
        key_field_ids = {fid for fid in key_field_ids if fid}
        after = None
        while True:
            idata = gh.graphql(PROJECT_ITEMS_QUERY, {"projectId": project_node_id, "after": after})
            node = (idata.get("node") or {})
            items_conn = (node.get("items") or {})
            nodes = items_conn.get("nodes") or []
            for it in nodes:
                fv_nodes = (((it.get("fieldValues") or {}).get("nodes")) or [])
                for fv in fv_nodes:
                    f = fv.get("field") or {}
                    fid = str(f.get("id") or "").strip()
                    if fid not in key_field_ids:
                        continue
                    key_val = ""
                    if fv.get("__typename") == "ProjectV2ItemFieldTextValue":
                        key_val = str(fv.get("text") or "").strip()
                    elif fv.get("__typename") == "ProjectV2ItemFieldSingleSelectValue":
                        key_val = str(fv.get("name") or "").strip()
                    if key_val:
                        existing_by_key[key_val] = it
            pi = (items_conn.get("pageInfo") or {})
            if not pi.get("hasNextPage"):
                break
            after = pi.get("endCursor")

        # Upsert items (draft issues only for MVP)
        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}
        errors: list[str] = []

        def set_field(item_id: str, field_key: str, value: dict[str, Any]) -> None:
            fid = resolved_fields[field_key]["id"]
            gh.graphql(UPDATE_ITEM_FIELD_MUTATION, {"projectId": project_node_id, "itemId": item_id, "fieldId": fid, "value": value})

        for it in desired:
            item = existing_by_key.get(it.key)
            item_id = str((item or {}).get("id") or "").strip()
            item_content = ((item or {}).get("content") or {}) if isinstance((item or {}).get("content"), dict) else {}
            is_new = False
            if not item_id:
                try:
                    r = gh.graphql(ADD_DRAFT_ISSUE_MUTATION, {"projectId": project_node_id, "title": it.title, "body": it.body})
                    item_id = str((((r.get("addProjectV2DraftIssue") or {}).get("projectItem")) or {}).get("id") or "").strip()
                    is_new = True
                    stats["created"] += 1
                except Exception as e:
                    stats["errors"] += 1
                    errors.append(f"create_item_failed key={it.key}: {e}")
                    continue
            else:
                try:
                    if str(item_content.get("__typename") or "") == "DraftIssue":
                        draft_issue_id = str(item_content.get("id") or "").strip()
                        current_title = str(item_content.get("title") or "").strip()
                        current_body = str(item_content.get("body") or "").strip()
                        if draft_issue_id and (current_title != it.title or current_body != it.body):
                            gh.graphql(UPDATE_DRAFT_ISSUE_MUTATION, {"draftIssueId": draft_issue_id, "title": it.title, "body": it.body})
                except Exception as e:
                    stats["errors"] += 1
                    errors.append(f"update_draft_issue_failed key={it.key}: {e}")
                    continue

            # Prepare values
            try:
                for field_key, raw_value in it.field_values.items():
                    field_meta = resolved_fields.get(field_key)
                    if not field_meta:
                        continue
                    ftype = str(field_meta["type"]).upper()
                    if ftype == "TEXT":
                        set_field(item_id, field_key, _field_value_input("TEXT", text=str(raw_value)))
                    elif ftype == "NUMBER":
                        set_field(item_id, field_key, _field_value_input("NUMBER", number=float(raw_value or 0)))
                    elif ftype == "DATE" and str(raw_value).strip():
                        set_field(item_id, field_key, _field_value_input("DATE", date=str(raw_value)))
                    elif ftype == "SINGLE_SELECT":
                        option = field_meta["options_by_name"].get(str(raw_value))
                        if option and option.get("id"):
                            set_field(item_id, field_key, _field_value_input("SINGLE_SELECT", single_select_option_id=str(option["id"])))

                stats["updated"] += 0 if is_new else 1
            except Exception as e:
                stats["errors"] += 1
                errors.append(f"update_fields_failed key={it.key}: {e}")

        return {
            "project_id": project_id,
            "mode": mode,
            "dry_run": False,
            "mapping_sha256": mapping.sha256,
            "project_url": project_url,
            "stats": stats,
            "errors": errors[:50],
        }
