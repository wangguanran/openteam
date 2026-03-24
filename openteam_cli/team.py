"""Team subcommand handlers."""
from __future__ import annotations

import argparse
import json
import subprocess
import textwrap
import urllib.parse
import urllib.request
from typing import Any

from openteam_cli._shared import (
    _base_url,
    _default_project_id,
    _find_openteam_repo_root,
    _norm,
    _runtime_root_for_repo,
)
from openteam_cli.http import _http_json, _iter_sse_events


def _team_status_doc(*, base_url: str = "") -> dict[str, Any]:
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return {}
    try:
        return _http_json("GET", url + "/v1/status")
    except Exception:
        return {}


def _default_team_id_from_status(status: dict[str, Any]) -> str:
    team_id = _norm(status.get("default_team_id"))
    if team_id:
        return team_id
    teams = status.get("teams") if isinstance(status, dict) else {}
    if isinstance(teams, dict) and teams:
        return sorted(str(key) for key in teams.keys() if str(key).strip())[0]
    return ""


def _team_summary_from_status(status: dict[str, Any], *, team_id: str) -> dict[str, Any]:
    teams = status.get("teams") if isinstance(status, dict) else {}
    if not isinstance(teams, dict):
        return {}
    team = teams.get(team_id)
    return dict(team) if isinstance(team, dict) else {}


def _read_last_team_run(repo_root: Any, *, base_url: str = "", team_id: str = "") -> dict[str, Any]:
    _ = repo_root
    status = _team_status_doc(base_url=base_url)
    wanted_team_id = str(team_id or "").strip() or _default_team_id_from_status(status)
    if not wanted_team_id:
        return {}
    team = _team_summary_from_status(status, team_id=wanted_team_id)
    last = team.get("last_run") if isinstance(team, dict) else {}
    return last if isinstance(last, dict) else {}


def _resolve_team_watch_run_id(base: str, *, team_id: str, project_id: str, explicit_run_id: str) -> str:
    rid = str(explicit_run_id or "").strip()
    if rid:
        return rid
    query = ""
    if str(project_id or "").strip():
        query = "?project_id=" + urllib.parse.quote(str(project_id or "").strip(), safe="")
    out = _http_json("GET", base + "/v1/runs" + query)
    runs = list(out.get("runs") or [])
    marker = f"team:{str(team_id or '').strip().lower()}"
    for item in runs:
        state = str(item.get("state") or "").strip().upper()
        objective = str(item.get("objective") or "").strip().lower()
        if state == "RUNNING" and marker in objective:
            rid = str(item.get("run_id") or "").strip()
            if rid:
                return rid
    return ""


def _resolve_team_run_id(base: str, *, team_id: str, project_id: str, explicit_run_id: str) -> str:
    rid = str(explicit_run_id or "").strip()
    if rid:
        return rid
    query = ""
    if str(project_id or "").strip():
        query = "?project_id=" + urllib.parse.quote(str(project_id or "").strip(), safe="")
    out = _http_json("GET", base + "/v1/runs" + query)
    runs = list(out.get("runs") or [])
    marker = f"team:{str(team_id or '').strip().lower()}"
    for item in runs:
        state = str(item.get("state") or "").strip().upper()
        objective = str(item.get("objective") or "").strip().lower()
        if state == "RUNNING" and marker in objective:
            rid = str(item.get("run_id") or "").strip()
            if rid:
                return rid
    status = _team_status_doc(base_url=base)
    team = _team_summary_from_status(status, team_id=team_id)
    last_run = team.get("last_run") if isinstance(team, dict) else {}
    if isinstance(last_run, dict):
        rid = str(last_run.get("run_id") or "").strip()
    return rid


def _format_team_watch_event(item: dict[str, Any]) -> str:
    event_type = str(item.get("event_type") or "")
    actor = str(item.get("actor") or "")
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    if event_type.endswith("_PLANNING_TASK_OUTPUT"):
        agent = str(payload.get("agent") or "agent")
        task_name = str(payload.get("task_name") or "task")
        raw = str(payload.get("raw") or "(empty)").rstrip()
        return "\n".join([f"[planning] {agent} :: {task_name}", textwrap.indent(raw or "(empty)", "  ")])
    detail_parts = []
    for key in ("stage", "reason", "lane", "workflow_id", "title", "status", "records", "bug_findings", "proposal_id", "task_id", "target_id", "module", "state", "action"):
        value = payload.get(key)
        if value in ("", None, [], {}):
            continue
        detail_parts.append(f"{key}={value}")
    detail = "; ".join(detail_parts)
    if not detail and payload:
        raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        detail = raw_payload if len(raw_payload) <= 220 else raw_payload[:217] + "..."
    line = f"[event] {str(item.get('ts') or '')} {event_type}"
    if actor:
        line += f" actor={actor}"
    if detail:
        line += f" :: {detail}"
    return line


def _default_runtime_control_plane_container() -> str:
    proc = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.service=control-plane",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker ps failed: {(proc.stderr or proc.stdout or '').strip()[:300]}")
    names = [str(line).strip() for line in (proc.stdout or "").splitlines() if str(line).strip()]
    if not names:
        raise RuntimeError("No running control-plane container found. Start the runtime first.")
    for name in names:
        if "control-plane" in name:
            return name
    return names[0]


def cmd_team_list(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    out = _http_json("GET", base + "/v1/teams")
    teams = list(out.get("teams") or [])
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"teams_total={len(teams)}")
    if not teams:
        print("(none)")
        return
    rows: list[list[str]] = []
    for team in teams:
        rows.append(
            [
                str(team.get("team_id") or ""),
                str(team.get("display_name_zh") or "")[:20],
                ",".join([str(x) for x in (team.get("workflow_ids") or [])]),
                str(team.get("mission") or "")[:80],
            ]
        )
    from openteam_cli._shared import _fmt_table
    print(_fmt_table(["team_id", "display_name_zh", "workflow_ids", "mission"], rows))


def cmd_team_run(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    team_id = str(getattr(args, "team_id", "") or "").strip()
    if not team_id:
        raise RuntimeError("team_id is required")
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    explicit_repo = str(getattr(args, "repo_path", "") or "").strip()
    repo_url = str(getattr(args, "repo_url", "") or "").strip()
    target_id = str(getattr(args, "target_id", "") or "").strip()
    from pathlib import Path
    target_repo = Path(explicit_repo).expanduser().resolve() if explicit_repo else repo_root
    include_repo_path = bool(explicit_repo) or (not target_id and not repo_url)
    payload = {
        "project_id": _default_project_id(prof, args),
        "workstream_id": str(getattr(args, "workstream", "") or "general").strip() or "general",
        "objective": str(getattr(args, "objective", "") or f"CLI-triggered team:{team_id}").strip(),
        "target_id": target_id or None,
        "repo_path": str(target_repo) if include_repo_path else "",
        "repo_url": repo_url or None,
        "repo_locator": str(getattr(args, "repo_locator", "") or "").strip(),
        "dry_run": bool(args.dry_run),
        "force": bool(args.force),
        "trigger": "cli",
    }
    out = _http_json("POST", base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/run", payload)
    if not bool(out.get("ok")):
        raise RuntimeError(str(out.get("error") or "team_run_failed"))
    if bool(getattr(args, "quiet", False)):
        return
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_team_watch(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    team_id = str(getattr(args, "team_id", "") or "").strip()
    if not team_id:
        raise RuntimeError("team_id is required")
    project_id = str(getattr(args, "project_id", "") or _default_project_id(prof, args)).strip()
    run_id = _resolve_team_watch_run_id(
        base,
        team_id=team_id,
        project_id=project_id,
        explicit_run_id=str(getattr(args, "run_id", "") or "").strip(),
    )
    if not run_id:
        raise RuntimeError("No active team run found. Pass --run-id or start one first.")
    url = base + f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/stream"
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=int(getattr(args, "timeout", 3600) or 3600)) as resp:
        for item in _iter_sse_events(resp):
            event = str(item.get("event") or "")
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            if bool(getattr(args, "json", False)):
                print(json.dumps({"event": event, "data": data}, ensure_ascii=False))
                continue
            if event == "run":
                run = data.get("run") if isinstance(data.get("run"), dict) else {}
                print(
                    f"[run] run_id={run.get('run_id','')} state={run.get('state','')} "
                    f"project_id={run.get('project_id','')} objective={run.get('objective','')}"
                )
                continue
            if event == "agent":
                print(
                    f"[agent] {data.get('role_id','')} state={data.get('state','')} "
                    f"task={data.get('task_id','')} action={data.get('current_action','')}"
                )
                continue
            if event == "runtime_event":
                print(_format_team_watch_event(data))
                continue
            if event == "end":
                run = data.get("run") if isinstance(data.get("run"), dict) else {}
                state = str(run.get("state") or data.get("state") or "").strip()
                print(f"[end] run_id={run.get('run_id', run_id)} state={state or 'DONE'}")
                return


def cmd_team_proposals(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    query: dict[str, str] = {}
    target_id = _norm(getattr(args, "target_id", "") or "")
    project_id = _norm(getattr(args, "project_id", "") or "")
    lane = _norm(getattr(args, "lane", "") or "")
    status = _norm(getattr(args, "status", "") or "")
    if target_id:
        query["target_id"] = target_id
    if project_id:
        query["project_id"] = project_id
    if lane:
        query["lane"] = lane
    if status:
        query["status"] = status
    url = base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/proposals"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    out = _http_json("GET", url)
    proposals = list(out.get("proposals") or [])
    if bool(getattr(args, "json", False)):
        print(json.dumps({"total": len(proposals), "proposals": proposals}, ensure_ascii=False, indent=2))
        return
    print(f"proposals_total={len(proposals)}")
    if not proposals:
        print("(none)")
        return
    rows: list[list[str]] = []
    for p in proposals:
        rows.append(
            [
                str(p.get("proposal_id") or ""),
                str(p.get("lane") or ""),
                str(p.get("status") or ""),
                str(p.get("version_bump") or ""),
                str(p.get("target_version") or ""),
                str(p.get("cooldown_until") or ""),
                str(p.get("discussion_issue_url") or "")[:48],
                str(p.get("title") or "")[:60],
            ]
        )
    from openteam_cli._shared import _fmt_table
    print(_fmt_table(["proposal_id", "lane", "status", "bump", "target", "cooldown_until", "discussion_issue", "title"], rows))


def cmd_team_decide(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    payload = {
        "proposal_id": str(args.proposal_id),
        "action": str(args.action),
        "title": str(getattr(args, "title", "") or "").strip() or None,
        "summary": str(getattr(args, "summary", "") or "").strip() or None,
        "version_bump": str(getattr(args, "version_bump", "") or "").strip() or None,
    }
    out = _http_json("POST", base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/proposals/decide", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    proposal = out.get("proposal") or {}
    print(f"proposal_id={proposal.get('proposal_id','')}")
    print(f"status={proposal.get('status','')}")
    print(f"lane={proposal.get('lane','')}")
    print(f"title={proposal.get('title','')}")
    print(f"version_bump={proposal.get('version_bump','')}")
    print(f"target_version={proposal.get('target_version','')}")
    print(f"discussion_issue_url={proposal.get('discussion_issue_url','')}")


def cmd_team_discussions_sync(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    out = _http_json("POST", base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/discussions/sync", {})
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"ok={bool(out.get('ok'))}")
    print(f"scanned={out.get('scanned', 0)}")
    print(f"updated={out.get('updated', 0)}")
    print(f"replied={out.get('replied', 0)}")
    print(f"errors={out.get('errors', 0)}")


def cmd_team_coding_run(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    payload = {
        "project_id": _default_project_id(prof, args),
        "target_id": str(getattr(args, "target_id", "") or "").strip() or None,
        "task_id": str(getattr(args, "task_id", "") or "").strip() or None,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "force": bool(getattr(args, "force", False)),
        "concurrency": int(getattr(args, "concurrency", 10) or 10),
    }
    out = _http_json("POST", base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/coding/run", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"ok={bool(out.get('ok'))}")
    print(f"scanned={out.get('scanned', 0)}")
    print(f"processed={out.get('processed', 0)}")
    summary = out.get("summary") or {}
    if summary:
        print(f"coding.total={summary.get('total', 0)}")
        print(f"coding.queued={summary.get('queued', 0)}")
        print(f"coding.active={summary.get('coding', 0)}")
        print(f"coding.blocked={summary.get('blocked', 0)}")


def cmd_team_coding_tasks(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    query: dict[str, str] = {}
    project_id = _default_project_id(prof, args)
    target_id = _norm(getattr(args, "target_id", "") or "")
    status = _norm(getattr(args, "status", "") or "")
    if project_id:
        query["project_id"] = project_id
    if target_id:
        query["target_id"] = target_id
    if status:
        query["status"] = status
    url = base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/coding/tasks"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    out = _http_json("GET", url)
    tasks = list(out.get("tasks") or [])
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    summary = out.get("summary") or {}
    print(f"coding_tasks_total={len(tasks)}")
    if summary:
        print(f"coding.queued={summary.get('queued', 0)} coding.active={summary.get('coding', 0)} coding.blocked={summary.get('blocked', 0)}")
    if not tasks:
        print("(none)")
        return
    rows: list[list[str]] = []
    for task in tasks:
        rows.append(
            [
                str(task.get("task_id") or ""),
                str(task.get("status") or ""),
                str(task.get("stage") or ""),
                str(task.get("owner_role") or ""),
                str(task.get("attempt_count") or ""),
                str(task.get("pull_request_url") or "")[:48],
                str(task.get("title") or "")[:60],
            ]
        )
    from openteam_cli._shared import _fmt_table
    print(_fmt_table(["task_id", "status", "stage", "owner_role", "attempts", "pull_request", "title"], rows))


def cmd_team_logs(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    run_id = str(getattr(args, "run_id", "") or "").strip()
    if not run_id:
        status = _team_status_doc(base_url=base)
        team = _team_summary_from_status(status, team_id=team_id)
        last_run = team.get("last_run") if isinstance(team, dict) else {}
        if isinstance(last_run, dict):
            run_id = str(last_run.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeError("No team run found. Start one first or pass --run-id.")
    limit = max(1, int(getattr(args, "limit", 200) or 200))
    url = base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/runs/{urllib.parse.quote(run_id, safe='')}/logs?limit={limit}"
    out = _http_json("GET", url)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    run = out.get("run") or {}
    saved_logs = out.get("saved_logs") if isinstance(out.get("saved_logs"), dict) else {}
    print("Team Run")
    print("========")
    print(f"team_id: {team_id}")
    print(f"run_id: {run.get('run_id','')}")
    print(f"state: {run.get('state','')}")
    print(f"project_id: {run.get('project_id','')}")
    print(f"workstream_id: {run.get('workstream_id','')}")
    print(f"objective: {run.get('objective','')}")
    print(f"report_available: {bool(out.get('report_available'))}")
    print(f"planning_agent_logs: {len(list(out.get('planning_agent_logs') or []))}")
    print(f"events: {len(list(out.get('events') or []))}")
    if out.get("summary"):
        print(f"summary: {out.get('summary')}")
    if saved_logs:
        print()
        print("Saved Logs")
        print("----------")
        if saved_logs.get("markdown_path"):
            print(f"markdown: {saved_logs.get('markdown_path')}")
        if saved_logs.get("json_path"):
            print(f"json: {saved_logs.get('json_path')}")
    print()
    print("Planning Agent Logs")
    print("-------------------")
    planning_logs = list(out.get("planning_agent_logs") or [])
    if not planning_logs:
        print("(none)")
    for idx, item in enumerate(planning_logs, start=1):
        task_name = str(item.get("task_name") or "").strip() or "task"
        agent = str(item.get("agent") or "").strip() or "agent"
        raw = str(item.get("raw") or "").strip() or "(empty)"
        print()
        print(f"{idx}. {agent} :: {task_name}")
        print("-" * 72)
        print(textwrap.indent(raw, "  "))
    print()
    print("Event Timeline")
    print("--------------")
    events = list(out.get("events") or [])
    if not events:
        print("(none)")
    for item in events:
        print(_format_team_watch_event(item))


def cmd_team_bug_scan_live(args: argparse.Namespace) -> None:
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    container = str(getattr(args, "container", "") or "").strip() or _default_runtime_control_plane_container()
    cmd = [
        "docker",
        "exec",
        "-i",
        container,
        "python",
        "/openteam/scripts/runtime/team_bug_scan_live.py",
        "--team-id",
        team_id,
        "--target-id",
        str(getattr(args, "target_id", "") or "").strip(),
    ]
    project_id = str(getattr(args, "project_id", "") or "").strip()
    if project_id:
        cmd.extend(["--project-id", project_id])
    if bool(getattr(args, "json", False)):
        cmd.append("--json")
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"team bug-scan-live failed rc={proc.returncode}")
