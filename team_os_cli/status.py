"""Status, focus, agents, tasks subcommand handlers."""
from __future__ import annotations

import argparse
import json
import urllib.parse
from typing import Any

from team_os_cli._shared import (
    _agent_is_active,
    _base_url,
    _default_project_id,
    _display_task_state,
    _find_team_os_repo_root,
    _fmt_table,
    _norm,
)
from team_os_cli.http import _http_json
from team_os_cli.team import (
    _default_team_id_from_status,
    _read_last_team_run,
    _team_status_doc,
    _team_summary_from_status,
)


def cmd_status(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    st = _team_status_doc(base_url=base)
    instance_id = st.get("instance_id", "")
    focus = st.get("current_focus") or {}
    default_team_id = _default_team_id_from_status(st)
    default_team = _team_summary_from_status(st, team_id=default_team_id) if default_team_id else {}

    project_id = _default_project_id(prof, args)
    workstream_id = args.workstream

    pending = st.get("pending_decisions") or []
    if pending and (not getattr(args, "all_decisions", False)):
        # Default: show pending decisions relevant to the selected project.
        tasks_all = st.get("tasks") or []
        task_to_project: dict[str, str] = {}
        for t in tasks_all:
            tid = _norm(t.get("task_id"))
            pid = _norm(t.get("project_id"))
            if tid and pid:
                task_to_project[tid] = pid

        filtered: list[dict[str, Any]] = []
        for d in pending:
            pid = _norm(d.get("project_id"))
            if pid:
                if pid == project_id:
                    filtered.append(d)
                continue
            tid = _norm(d.get("task_id"))
            if tid and task_to_project.get(tid) == project_id:
                filtered.append(d)
        pending = filtered
    if pending:
        print(f"PENDING_DECISIONS ({len(pending)}) profile={prof['name']} instance_id={instance_id}")
        for i, d in enumerate(pending, 1):
            dtype = str(d.get("type") or "").strip()
            pid = str(d.get("project_id") or "").strip()
            rid = str(d.get("req_id") or "").strip()
            tid = str(d.get("task_id") or "").strip()
            key = rid or tid
            print(f"{i}. {dtype} {pid} {key}".strip())
        print()

    print(f"profile={prof['name']} instance_id={instance_id}")
    if st.get("workspace_root") is not None:
        print(f"workspace_root={st.get('workspace_root','')}")
    if st.get("workspace_projects_count") is not None:
        print(f"workspace_projects_count={st.get('workspace_projects_count','')}")
    print(f"focus.objective={focus.get('objective','')}")
    print(f"focus.updated_at={focus.get('updated_at','')}")
    repo_root = _find_team_os_repo_root()
    if repo_root:
        last = _read_last_team_run(repo_root, base_url=base, team_id=default_team_id) or {}
        if last.get("ts"):
            print(f"team.last_run_at={last.get('ts')}")
            print(f"team.status={last.get('status','')}")
            if last.get("records") is not None:
                print(f"team.records={last.get('records')}")
    if default_team_id:
        print(f"default_team_id={default_team_id}")
    proposal_counts = default_team.get("proposal_counts") or {}
    if proposal_counts:
        print(f"team.pending_proposals={proposal_counts.get('pending', 0)}")
        print(f"team.proposals_total={proposal_counts.get('total', 0)}")
    coding = default_team.get("coding") or {}
    delivery_summary = coding.get("summary") or {}
    if delivery_summary:
        print(f"team.coding_total={delivery_summary.get('total', 0)}")
        print(f"team.coding_queued={delivery_summary.get('queued', 0)}")
        print(f"team.coding_active={delivery_summary.get('coding', 0)}")
        print(f"team.coding_blocked={delivery_summary.get('blocked', 0)}")
    print()

    agents = st.get("agents") or []
    if project_id:
        agents = [a for a in agents if a.get("project_id") == project_id]
    if workstream_id:
        agents = [a for a in agents if a.get("workstream_id") == workstream_id]

    active_agents = [a for a in agents if _agent_is_active(a.get("state"))]
    active_tasks_by_id: dict[str, list[dict[str, Any]]] = {}
    for a in active_agents:
        tid = _norm(a.get("task_id"))
        if tid:
            active_tasks_by_id.setdefault(tid, []).append(a)

    print(
        f"active_agents={len(active_agents)} active_tasks={len(active_tasks_by_id)} "
        f"(project_id={project_id}{' workstream_id='+workstream_id if workstream_id else ''})"
    )
    if active_tasks_by_id:
        rows = []
        for tid in sorted(active_tasks_by_id.keys()):
            group = active_tasks_by_id[tid]
            roles = ",".join(sorted({str(x.get('role_id', '')) for x in group if str(x.get('role_id', '')).strip()}))
            last_hb = max([_norm(x.get("last_heartbeat")) for x in group], default="")
            rows.append([tid, str(len(group)), roles[:40], last_hb])
        print(_fmt_table(["task", "agents", "roles", "last_heartbeat"], rows))
    else:
        print("(no active tasks)")
    print()

    rows = []
    for a in agents:
        rows.append(
            [
                str(a.get("agent_id", ""))[:8],
                str(a.get("role_id", "")),
                str(a.get("state", "")),
                str(a.get("task_id", "")),
                str(a.get("current_action", ""))[:40],
                str(a.get("last_heartbeat", "")),
            ]
        )
    print(
        f"agents_total={len(rows)} active={len(active_agents)} "
        f"(project_id={project_id}{' workstream_id='+workstream_id if workstream_id else ''})"
    )
    if rows:
        print(_fmt_table(["agent", "role", "state", "task", "action", "heartbeat"], rows))
    else:
        print("(none)")
    print()

    tasks = st.get("tasks") or []
    if project_id:
        tasks = [t for t in tasks if t.get("project_id") == project_id]
    if workstream_id:
        tasks = [t for t in tasks if t.get("workstream_id") == workstream_id]
    rows = []
    for t in tasks:
        tid = _norm(t.get("task_id"))
        agents_n = len(active_tasks_by_id.get(tid, [])) if tid else 0
        rows.append(
            [
                tid,
                _display_task_state(t.get("state", "")),
                str(t.get("owner_role", "")),
                str(t.get("workstream_id", "")),
                "YES" if t.get("need_pm_decision") else "",
                str(agents_n),
            ]
        )
    print(f"tasks={len(rows)}")
    if rows:
        print(_fmt_table(["task_id", "state", "owner", "workstream", "NEED_PM", "agents"], rows))
    else:
        print("(none)")


def cmd_focus(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    if args.set:
        payload = {"objective": args.set}
        out = _http_json("POST", base + "/v1/focus", payload)
        print(f"updated objective={out.get('objective','')}")
        return
    out = _http_json("GET", base + "/v1/focus")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_agents(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args) if not args.all else None
    q = {}
    if project_id:
        q["project_id"] = project_id
    if args.workstream:
        q["workstream_id"] = args.workstream
    if args.state:
        q["state"] = args.state
    if args.role:
        q["role_id"] = args.role
    url = base + "/v1/agents"
    if q:
        url += "?" + urllib.parse.urlencode(q)
    out = _http_json("GET", url)
    agents = out.get("agents") or []
    rows = []
    for a in agents:
        rows.append(
            [
                str(a.get("agent_id", ""))[:8],
                str(a.get("role_id", "")),
                str(a.get("project_id", "")),
                str(a.get("workstream_id", "")),
                str(a.get("task_id", "")),
                str(a.get("state", "")),
                str(a.get("current_action", ""))[:50],
                str(a.get("last_heartbeat", "")),
            ]
        )
    if rows:
        print(_fmt_table(["agent", "role", "project", "workstream", "task", "state", "action", "heartbeat"], rows))
    else:
        print("(none)")


def cmd_tasks(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args) if args.project or not args.all else None
    q = {"limit": args.limit, "offset": args.offset}
    if project_id:
        q["project_id"] = project_id
    if args.workstream:
        q["workstream_id"] = args.workstream
    if args.state:
        # Backward compatible aliases: running/work -> doing
        st = str(args.state or "").strip().lower()
        if st in ("running", "work", "in_progress", "inprogress"):
            st = "doing"
        q["state"] = st
    url = base + "/v1/tasks?" + urllib.parse.urlencode(q)
    out = _http_json("GET", url)
    tasks = out.get("tasks") or []
    rows = []
    for t in tasks:
        rows.append(
            [
                str(t.get("task_id", "")),
                _display_task_state(t.get("state", "")),
                str(t.get("owner_role", "")),
                str(t.get("project_id", "")),
                str(t.get("workstream_id", "")),
                "YES" if t.get("need_pm_decision") else "",
                str(t.get("risk", "")),
            ]
        )
    if rows:
        print(_fmt_table(["task_id", "state", "owner", "project", "workstream", "NEED_PM", "risk"], rows))
    else:
        print("(none)")
