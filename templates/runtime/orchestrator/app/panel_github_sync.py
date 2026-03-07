import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import yaml

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
from .state_store import ledger_tasks_dir, load_focus, runtime_state_root, team_os_root, teamos_requirements_dir
from .workspace_store import ensure_project_scaffold, ledger_tasks_dir as ws_ledger_tasks_dir, requirements_dir as ws_requirements_dir


class PanelSyncError(Exception):
    pass


@dataclass(frozen=True)
class DesiredItem:
    key: str  # stored in Task ID field for stable mapping
    kind: str  # TASK|DECISION|MILESTONE
    title: str
    body: str
    workstreams: list[str]
    status_key: str  # TeamOS Status option key (e.g. TODO)
    risk_key: str  # Risk option key (e.g. LOW)
    need_pm: bool
    focus: str
    active_agents: int
    last_heartbeat: str  # ISO-8601 or ""
    start_date: str  # YYYY-MM-DD or ""
    target_date: str  # YYYY-MM-DD or ""
    links_text: str
    repo_locator: str
    repo_mode: str


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
    if raw in ("process", "ops", "maintenance"):
        return "Process"
    return "Feature"


def _panel_item_title(raw_title: str, *, kind: str = "", lane: str = "", module: str = "") -> str:
    title = _norm(raw_title)
    if not title:
        return "[Feature][General] 未命名事项"
    if re.match(r"^\[(Bug|Feature|Process)\]\[[^\]]+\]\s+\S+", title):
        return title
    mod = _norm(module) or "General"
    return f"[{_issue_style_type(kind=kind, lane=lane)}][{mod}] {title}"


def _load_tasks(project_id: str) -> list[dict[str, Any]]:
    if str(project_id) == "teamos":
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
    if str(project_id) == "teamos":
        req_dir = teamos_requirements_dir()
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
    if str(project_id) == "teamos":
        req_dir = teamos_requirements_dir()
    else:
        ensure_project_scaffold(project_id)
        req_dir = ws_requirements_dir(project_id)
    y = req_dir / "requirements.yaml"
    if not y.exists():
        return []
    data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
    return list(data.get("requirements") or [])


def _load_self_upgrade_feature_proposals(project_id: str) -> list[dict[str, Any]]:
    path = runtime_state_root() / "self_upgrade_proposals.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else {}
    if not isinstance(items, dict):
        return []
    out: list[dict[str, Any]] = []
    for proposal_id, raw in items.items():
        doc = raw if isinstance(raw, dict) else {}
        if str(doc.get("project_id") or "teamos").strip() != str(project_id):
            continue
        lane = str(doc.get("lane") or "").strip().lower()
        if lane not in ("feature", "process"):
            continue
        status = str(doc.get("status") or "").strip().upper()
        if status in ("REJECTED", "MATERIALIZED"):
            continue
        out.append({"proposal_id": proposal_id, **doc})
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

    items: list[DesiredItem] = []

    # Tasks
    for t in _load_tasks(project_id):
        tid = str(t.get("id") or "").strip()
        title = _panel_item_title(
            str(t.get("title") or "").strip(),
            kind=str((((t.get("self_upgrade") or {}) if isinstance(t.get("self_upgrade"), dict) else {}).get("kind")) or ""),
            lane=str((((t.get("self_upgrade") or {}) if isinstance(t.get("self_upgrade"), dict) else {}).get("lane")) or ""),
            module=str((((t.get("self_upgrade") or {}) if isinstance(t.get("self_upgrade"), dict) else {}).get("module")) or (((t.get("execution_policy") or {}) if isinstance(t.get("execution_policy"), dict) else {}).get("module")) or ""),
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
                workstreams=[wsid],
                status_key=status_key_from_ledger(state),
                risk_key=risk_key_from_ledger(risk),
                need_pm=need_pm,
                focus=focus_obj,
                active_agents=len(assigned),
                last_heartbeat=last_hb,
                start_date=_parse_date_from_iso(str(t.get("start_date") or "")),
                target_date=_parse_date_from_iso(str(t.get("target_date") or "")),
                links_text=links_text,
                repo_locator=repo_locator,
                repo_mode=repo_mode,
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
                workstreams=[str(x) for x in ws],
                status_key=status_key,
                risk_key="LOW",
                need_pm=(st in ("CONFLICT",)),
                focus=focus_obj,
                active_agents=0,
                last_heartbeat="",
                start_date="",
                target_date="",
                links_text="",
                repo_locator="",
                repo_mode="",
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
                workstreams=[str(x) for x in ws],
                status_key="BLOCKED",
                risk_key="MED",
                need_pm=True,
                focus=focus_obj,
                active_agents=0,
                last_heartbeat="",
                start_date="",
                target_date="",
                links_text=links_text,
                repo_locator="",
                repo_mode="",
            )
        )

    # Self-upgrade proposals waiting for discussion / confirmation.
    for p in _load_self_upgrade_feature_proposals(project_id):
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
                workstreams=[str(p.get("workstream_id") or "general").strip() or "general"],
                status_key=status_key,
                risk_key="MED",
                need_pm=True,
                focus=focus_obj,
                active_agents=0,
                last_heartbeat=str(p.get("discussion_reply_updated_at") or p.get("updated_at") or ""),
                start_date="",
                target_date=_parse_date_from_iso(str(p.get("cooldown_until") or "")),
                links_text=links_text,
                repo_locator=str(p.get("repo_locator") or "").strip(),
                repo_mode="proposal",
            )
        )

    # Milestones from plan overlay
    for m in list_milestones(project_id):
        links_text = _join_links(m.links)
        items.append(
            DesiredItem(
                key=f"MILESTONE:{m.milestone_id}",
                kind="MILESTONE",
                title=f"[MILESTONE] {m.milestone_id} {m.title}".strip(),
                body="\n".join(
                    [
                        f"Milestone: {m.milestone_id}",
                        "",
                        m.objective or "",
                        "",
                        (
                            f"Plan: docs/plan/teamos/plan.yaml"
                            if str(project_id) == "teamos"
                            else f"Plan: <WORKSPACE>/projects/{project_id}/state/plan/plan.yaml"
                        ),
                        "",
                        "Links:",
                        links_text or "(none)",
                    ]
                ).strip()
                + "\n",
                workstreams=[str(x) for x in (m.workstreams or [])] or ["general"],
                status_key="TODO",
                risk_key="LOW",
                need_pm=False,
                focus=focus_obj,
                active_agents=0,
                last_heartbeat="",
                start_date=_parse_date_from_iso(m.start_date),
                target_date=_parse_date_from_iso(m.target_date),
                links_text=links_text,
                repo_locator="",
                repo_mode="",
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
    Sync TeamOS truth -> GitHub Projects v2 (view layer).

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
                mapping = MappingDoc(path=team_os_root() / "integrations" / "github_projects" / "mapping.yaml", sha256="missing", data={"projects": {}})
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
                        "workstreams": it.workstreams,
                        "status": it.status_key,
                        "risk": it.risk_key,
                        "need_pm_decision": it.need_pm,
                        "active_agents": it.active_agents,
                        "last_heartbeat": it.last_heartbeat,
                        "start_date": it.start_date,
                        "target_date": it.target_date,
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
        after = None
        while True:
            idata = gh.graphql(PROJECT_ITEMS_QUERY, {"projectId": project_node_id, "after": after})
            node = (idata.get("node") or {})
            items_conn = (node.get("items") or {})
            nodes = items_conn.get("nodes") or []
            for it in nodes:
                fv_nodes = (((it.get("fieldValues") or {}).get("nodes")) or [])
                # Find Task ID field value (text)
                key_val = ""
                task_id_field_id = resolved_fields["task_id"]["id"]
                for fv in fv_nodes:
                    f = fv.get("field") or {}
                    fid = str(f.get("id") or "").strip()
                    if fid != task_id_field_id:
                        continue
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
                # Task ID (mapping key)
                set_field(item_id, "task_id", _field_value_input("TEXT", text=it.key))

                # Status
                status_field = resolved_fields["teamos_status"]
                opt = status_field["options_by_name"].get(str((cfg.get("fields") or {}).get("teamos_status", {}).get("options", {}).get(it.status_key, {}).get("name") or it.status_key))
                # Fallback by option key -> name
                if not opt:
                    # try direct match on option name
                    for oname, o in status_field["options_by_name"].items():
                        if oname.strip().lower() == it.status_key.strip().lower():
                            opt = o
                            break
                if opt and opt.get("id"):
                    set_field(item_id, "teamos_status", _field_value_input("SINGLE_SELECT", single_select_option_id=str(opt["id"])))

                # Workstreams (text for MVP; comma-separated)
                set_field(item_id, "workstreams", _field_value_input("TEXT", text=",".join(sorted(set(it.workstreams)))))

                # Risk (single select)
                risk_field = resolved_fields["risk"]
                risk_name = str((cfg.get("fields") or {}).get("risk", {}).get("options", {}).get(it.risk_key, {}).get("name") or it.risk_key)
                opt = risk_field["options_by_name"].get(risk_name)
                if opt and opt.get("id"):
                    set_field(item_id, "risk", _field_value_input("SINGLE_SELECT", single_select_option_id=str(opt["id"])))

                # Need PM Decision (single select Yes/No)
                npm_field = resolved_fields["need_pm_decision"]
                yn_key = "YES" if it.need_pm else "NO"
                yn_name = str((cfg.get("fields") or {}).get("need_pm_decision", {}).get("options", {}).get(yn_key, {}).get("name") or ("Yes" if it.need_pm else "No"))
                opt = npm_field["options_by_name"].get(yn_name)
                if opt and opt.get("id"):
                    set_field(item_id, "need_pm_decision", _field_value_input("SINGLE_SELECT", single_select_option_id=str(opt["id"])))

                # Focus
                set_field(item_id, "current_focus", _field_value_input("TEXT", text=it.focus))

                # Agents + heartbeat
                set_field(item_id, "active_agents", _field_value_input("NUMBER", number=float(it.active_agents)))
                if it.last_heartbeat:
                    set_field(item_id, "last_heartbeat", _field_value_input("TEXT", text=it.last_heartbeat))

                # Roadmap dates
                if it.start_date:
                    set_field(item_id, "start_date", _field_value_input("DATE", date=it.start_date))
                if it.target_date:
                    set_field(item_id, "target_date", _field_value_input("DATE", date=it.target_date))

                # Links
                set_field(item_id, "links", _field_value_input("TEXT", text=it.links_text))

                # Repo locator + mode (operational context)
                set_field(item_id, "repo_locator", _field_value_input("TEXT", text=it.repo_locator))
                set_field(item_id, "repo_mode", _field_value_input("TEXT", text=it.repo_mode))

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
