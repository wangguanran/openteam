from __future__ import annotations

from pathlib import Path
from typing import Any

from app import crewai_workflow_registry
from app import improvement_store


def _proposal_runtime():
    from app.domains.team_workflow import proposal_runtime

    return proposal_runtime


def _task_runtime():
    from app.domains.team_workflow import task_runtime

    return task_runtime


def safe_project_id(project_id: str) -> str:
    return _proposal_runtime()._safe_project_id(project_id)


def resolve_target(*, target_id: str = "", repo_path: str = "", repo_url: str = "", repo_locator: str = "", project_id: str = "teamos") -> dict[str, Any]:
    return _proposal_runtime()._resolve_target(
        target_id=target_id,
        repo_path=repo_path,
        repo_url=repo_url,
        repo_locator=repo_locator,
        project_id=project_id,
    )


def team_os_root() -> Path:
    return _proposal_runtime().team_os_root()


def prepare_discovery_repo(*, source_repo_root: Path, target: dict[str, Any]) -> Path:
    return _proposal_runtime()._prepare_discovery_repo(source_repo_root=source_repo_root, target=target)


def collect_repo_context(*, repo_root: Path, scan_repo_root: Path, explicit_repo_locator: str = "", target_id: str = "") -> dict[str, Any]:
    return _proposal_runtime().collect_repo_context(
        repo_root=repo_root,
        scan_repo_root=scan_repo_root,
        explicit_repo_locator=explicit_repo_locator,
        target_id=target_id,
    )


def should_skip(*, target_id: str, repo_root: Path, force: bool) -> tuple[bool, str]:
    return _proposal_runtime()._should_skip(target_id=target_id, repo_root=repo_root, force=force)


def prompt_safe_repo_context(repo_context: dict[str, Any]) -> dict[str, Any]:
    return _proposal_runtime()._prompt_safe_repo_context(repo_context)


def bug_scan_policy(*, target_id: str, project_id: str, repo_context: dict[str, Any], force: bool) -> dict[str, Any]:
    return _proposal_runtime()._bug_scan_policy(
        target_id=target_id,
        project_id=project_id,
        repo_context=repo_context,
        force=force,
    )


def scan_limit(max_findings: int, lane_limit: int) -> int:
    return _proposal_runtime()._scan_limit(max_findings, lane_limit)


def structured_bug_scan_for_repo(
    *,
    team_id: str = "",
    repo_context: dict[str, Any],
    bug_scan_limit: int,
    bug_scan_dormant: bool,
    verbose: bool,
) -> tuple[Any, dict[str, Any]]:
    return _proposal_runtime()._structured_bug_scan_for_repo(
        team_id=str(team_id or "").strip(),
        repo_context=repo_context,
        bug_scan_limit=bug_scan_limit,
        bug_scan_dormant=bug_scan_dormant,
        verbose=verbose,
    )


def planned_version(current_version: str, findings: list[Any]) -> str:
    return _proposal_runtime()._planned_version(current_version, findings)


def default_work_items(*, repo_root: Path, finding: Any) -> list[dict[str, Any]]:
    return _proposal_runtime()._default_work_items(repo_root=repo_root, finding=finding)


def upsert_proposal(*, team_id: str, target_id: str, repo_root: Path, repo_locator: str, project_id: str, finding: Any, current_version: str) -> dict[str, Any]:
    return _proposal_runtime()._upsert_proposal(
        team_id=team_id,
        target_id=target_id,
        repo_root=repo_root,
        repo_locator=repo_locator,
        project_id=project_id,
        finding=finding,
        current_version=current_version,
    )


def record_from_materialized_item(
    *,
    team_id: str,
    target_id: str,
    repo_root: Path,
    repo_locator: str,
    project_id: str,
    finding: Any,
    work_item: Any,
    proposal_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    return _proposal_runtime()._record_from_materialized_item(
        team_id=team_id,
        target_id=target_id,
        repo_root=repo_root,
        repo_locator=repo_locator,
        project_id=project_id,
        finding=finding,
        work_item=work_item,
        proposal_id=proposal_id,
        dry_run=dry_run,
    )


def mark_proposal_materialized(proposal_id: str) -> dict[str, Any]:
    return _proposal_runtime()._mark_proposal_materialized(proposal_id)


def ensure_proposal_discussion_issue(proposal: dict[str, Any]) -> dict[str, Any]:
    return _proposal_runtime()._ensure_proposal_discussion_issue(proposal)


def update_proposal_record(
    proposal_id: str,
    *,
    title: str = "",
    summary: str = "",
    version_bump: str = "",
    status: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _proposal_runtime()._update_proposal_record(
        proposal_id,
        title=title,
        summary=summary,
        version_bump=version_bump,
        status=status,
        extra=extra,
    )


def discussion_issue_number(proposal: dict[str, Any]) -> int:
    return _proposal_runtime()._discussion_issue_number(proposal)


def proposal_due(proposal: dict[str, Any]) -> bool:
    return bool(_proposal_runtime()._proposal_due(proposal))


def sync_panel(*, db: Any, project_id: str) -> dict[str, Any]:
    return _proposal_runtime()._sync_panel(db=db, project_id=project_id)


def is_user_comment(comment: Any) -> bool:
    return bool(_proposal_runtime()._comment_is_user_comment(comment))


def proposal_action_from_comment_text(text: str) -> str:
    return _proposal_runtime()._proposal_action_from_comment_text(text)


def utc_now_iso() -> str:
    from team_os_common import utc_now_iso as _utc_now_iso
    return _utc_now_iso()


def load_yaml(path: Path) -> dict[str, Any]:
    return _task_runtime()._load_yaml(path)


def task_lane(task_doc: dict[str, Any]) -> str:
    return str(_task_runtime()._task_lane(task_doc) or "").strip()


def claim_delivery_task_lease(*, db: Any, actor: str, task: dict[str, Any]) -> dict[str, Any] | None:
    return _task_runtime()._claim_delivery_task_lease(db=db, actor=actor, task=task)


def execute_delivery_candidate(
    *,
    db: Any,
    actor: str,
    ledger_path: Path,
    dry_run: bool,
    force: bool,
    lease: dict[str, Any],
) -> dict[str, Any]:
    return _task_runtime()._execute_delivery_candidate(
        db=db,
        actor=actor,
        ledger_path=ledger_path,
        dry_run=dry_run,
        force=force,
        lease=lease,
    )


def read_state(target_id: str) -> dict[str, Any]:
    return _proposal_runtime()._read_state(str(target_id or "").strip())


def list_proposals(*, team_id: str, target_id: str = "", project_id: str = "", lane: str = "", status: str = "") -> list[dict[str, Any]]:
    return _proposal_runtime().list_proposals(
        team_id=team_id,
        target_id=target_id,
        project_id=project_id,
        lane=lane,
        status=status,
    )


def decide_proposal(
    *,
    team_id: str,
    proposal_id: str,
    action: str,
    title: str = "",
    summary: str = "",
    version_bump: str = "",
) -> dict[str, Any]:
    return _proposal_runtime().decide_proposal(
        team_id=team_id,
        proposal_id=proposal_id,
        action=action,
        title=title,
        summary=summary,
        version_bump=version_bump,
    )


def reconcile_discussions(
    *,
    team_id: str,
    db: Any,
    actor: str,
    verbose: bool = False,
    project_id: str = "",
    target_id: str = "",
) -> dict[str, Any]:
    return _proposal_runtime().reconcile_feature_discussions(
        db=db,
        actor=actor,
        verbose=verbose,
        project_id=project_id,
        target_id=target_id,
        team_id=team_id,
    )


def list_delivery_tasks(*, team_id: str, project_id: str = "", target_id: str = "", status: str = "") -> list[dict[str, Any]]:
    return _task_runtime().list_delivery_tasks(
        team_id=team_id,
        project_id=project_id,
        target_id=target_id,
        status=status,
    )


def delivery_summary(*, team_id: str, project_id: str = "", target_id: str = "") -> dict[str, Any]:
    return _task_runtime().delivery_summary(
        team_id=team_id,
        project_id=project_id,
        target_id=target_id,
    )


def run_delivery_sweep(
    *,
    team_id: str,
    db: Any,
    actor: str,
    project_id: str = "",
    target_id: str = "",
    task_id: str = "",
    dry_run: bool = False,
    force: bool = False,
    concurrency: int | None = None,
) -> dict[str, Any]:
    return _task_runtime().run_delivery_sweep(
        team_id=team_id,
        db=db,
        actor=actor,
        project_id=project_id,
        target_id=target_id,
        task_id=task_id,
        dry_run=dry_run,
        force=force,
        concurrency=concurrency,
    )


def migrate_legacy_worktrees(*, project_id: str = "", task_id: str = "") -> dict[str, Any]:
    return _task_runtime().migrate_legacy_worktrees(project_id=project_id, task_id=task_id)


def run_team_iteration(*, team_id: str, db: Any, spec: Any, actor: str, run_id: str, crewai_info: dict[str, Any]) -> dict[str, Any]:
    from app.engines.crewai.workflow_runner import WorkflowRunContext, run_workflow

    proposal_runtime = _proposal_runtime()

    normalized_team_id = str(team_id or "").strip()
    if not normalized_team_id:
        raise RuntimeError("team_id is required")

    project_id = proposal_runtime._safe_project_id(str(getattr(spec, "project_id", "teamos") or "teamos"))
    workstream_id = str(getattr(spec, "workstream_id", "") or "general").strip() or "general"
    target = proposal_runtime._resolve_target(
        target_id=str(getattr(spec, "target_id", "") or ""),
        repo_path=str(getattr(spec, "repo_path", "") or ""),
        repo_url=str(getattr(spec, "repo_url", "") or ""),
        repo_locator=str(getattr(spec, "repo_locator", "") or ""),
        project_id=project_id,
    )
    target_id = str(target.get("target_id") or "").strip() or "teamos"
    repo_root = Path(str(target.get("repo_root") or proposal_runtime.team_os_root())).expanduser().resolve()
    repo_locator = str(target.get("repo_locator") or "").strip()
    trigger = str(getattr(spec, "trigger", "") or "manual").strip() or "manual"
    dry_run = bool(getattr(spec, "dry_run", False))
    force = bool(getattr(spec, "force", False))

    workflows = [
        workflow
        for workflow in crewai_workflow_registry.list_workflows(team_id=normalized_team_id, project_id=project_id)
        if workflow.phase == crewai_workflow_registry.PHASE_FINDING and workflow.enabled
    ]
    if not workflows:
        payload = {
            "ok": True,
            "skipped": True,
            "reason": "no_enabled_workflows",
            "team_id": normalized_team_id,
            "run_id": run_id,
            "target_id": target_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "trigger": trigger,
            "crewai": crewai_info,
        }
        proposal_runtime._merge_state_last_run(
            target_id,
            {
                "ts": proposal_runtime._utc_now_iso(),
                "team_id": normalized_team_id,
                "target_id": target_id,
                "repo_root": str(repo_root),
                "repo_locator": repo_locator,
                "status": "SKIPPED",
                "reason": "no_enabled_workflows",
            },
        )
        db.add_event(
            event_type="TEAM_WORKFLOW_SKIPPED",
            actor=actor,
            project_id=project_id,
            workstream_id=workstream_id,
            payload=payload,
        )
        return payload

    db.add_event(
        event_type="TEAM_WORKFLOW_STARTED",
        actor=actor,
        project_id=project_id,
        workstream_id=workstream_id,
        payload={
            "team_id": normalized_team_id,
            "run_id": run_id,
            "target_id": target_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "trigger": trigger,
            "dry_run": dry_run,
        },
    )

    records: list[dict[str, Any]] = []
    pending_proposals: list[dict[str, Any]] = []
    workflow_results: list[dict[str, Any]] = []
    summaries: list[str] = []
    ci_actions: list[str] = []
    notes: list[str] = []
    panel_sync: dict[str, Any] = {}
    current_version = "0.1.0"
    planned_version = "0.1.0"
    bug_finding_count = 0
    repo_context_for_bug_state: dict[str, Any] = {}
    bug_scan_policy: dict[str, Any] = {}

    for workflow in workflows:
        runtime_policy = crewai_workflow_registry.evaluate_workflow_runtime_policy(
            workflow=workflow,
            target_id=target_id,
            force=force,
        )
        _ = crewai_workflow_registry.update_workflow_runtime_state(target_id, workflow.workflow_id, runtime_policy)
        if not runtime_policy.allowed:
            workflow_results.append(
                {
                    "workflow_id": workflow.workflow_id,
                    "ok": True,
                    "skipped": True,
                    "reason": runtime_policy.reason,
                }
            )
            continue

        result = run_workflow(
            context=WorkflowRunContext(
                db=db,
                workflow=workflow,
                actor=actor,
                project_id=project_id,
                workstream_id=workstream_id,
                target_id=target_id,
                dry_run=dry_run,
                force=force,
                run_id=run_id,
                crewai_info=crewai_info,
                extra={
                    "repo_path": str(getattr(spec, "repo_path", "") or ""),
                    "repo_url": str(getattr(spec, "repo_url", "") or ""),
                    "repo_locator": str(getattr(spec, "repo_locator", "") or ""),
                },
            )
        )
        workflow_results.append(result)
        materialized = dict((((result.get("state") or {}).get("tasks") or {}).get("materialize_plan") or {}).get("outputs") or {})
        if not materialized:
            continue
        summaries.append(str(materialized.get("summary") or "").strip())
        records.extend(list(materialized.get("records") or []))
        pending_proposals.extend(list(materialized.get("pending_proposals") or []))
        panel_sync = dict(materialized.get("panel_sync") or panel_sync)
        current_version = str(materialized.get("current_version") or current_version).strip() or current_version
        planned_version = str(materialized.get("planned_version") or planned_version).strip() or planned_version
        plan_doc = dict(materialized.get("plan") or {}) if isinstance(materialized.get("plan"), dict) else {}
        ci_actions.extend([str(item).strip() for item in list(plan_doc.get("ci_actions") or []) if str(item).strip()])
        notes.extend([str(item).strip() for item in list(plan_doc.get("notes") or []) if str(item).strip()])
        if workflow.lane == "bug":
            bug_finding_count = len(list((plan_doc.get("findings") or [])))
            repo_context_for_bug_state = dict((((result.get("state") or {}).get("tasks") or {}).get("prepare_context") or {}).get("outputs") or {}).get("repo_context") or {}
            bug_scan_policy = dict(materialized.get("bug_scan_policy") or {})

    if repo_context_for_bug_state:
        bug_lane_state = proposal_runtime._update_bug_lane_state(
            db=db,
            actor=actor,
            target_id=target_id,
            project_id=project_id,
            workstream_id=workstream_id,
            repo_context=repo_context_for_bug_state,
            bug_finding_count=bug_finding_count,
            policy=bug_scan_policy,
        )
    else:
        bug_lane_state = {}

    report = {
        "ts": proposal_runtime._utc_now_iso(),
        "run_id": run_id,
        "team_id": normalized_team_id,
        "target_id": target_id,
        "actor": actor,
        "trigger": trigger,
        "target": target,
        "repo_root": str(repo_root),
        "repo_locator": repo_locator,
        "project_id": project_id,
        "workflow_results": workflow_results,
        "records": records,
        "pending_proposals": pending_proposals,
        "panel_sync": panel_sync,
        "crewai": crewai_info,
    }
    improvement_store.save_report(target_id=target_id, project_id=project_id, report=report)
    proposal_runtime._merge_state_last_run(
        target_id,
        {
            "ts": proposal_runtime._utc_now_iso(),
            "run_id": run_id,
            "team_id": normalized_team_id,
            "target_id": target_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "status": "DONE",
            "records": len(records),
            "bug_findings": bug_finding_count,
            "bug_lane_status": str((bug_lane_state or {}).get("status") or "active"),
            "pending_proposals": len(pending_proposals),
            "report_id": run_id,
        },
    )
    proposal_runtime._append_run_history(
        target_id,
        {
            "ts": proposal_runtime._utc_now_iso(),
            "run_id": run_id,
            "team_id": normalized_team_id,
            "target_id": target_id,
            "status": "DONE",
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "records": len(records),
            "bug_findings": bug_finding_count,
            "bug_lane_status": str((bug_lane_state or {}).get("status") or "active"),
            "pending_proposals": len(pending_proposals),
        },
    )
    db.add_event(
        event_type="TEAM_WORKFLOW_FINISHED",
        actor=actor,
        project_id=project_id,
        workstream_id=workstream_id,
        payload={
            "team_id": normalized_team_id,
            "run_id": run_id,
            "target_id": target_id,
            "repo_root": str(repo_root),
            "repo_locator": repo_locator,
            "project_id": project_id,
            "records": len(records),
            "bug_findings": bug_finding_count,
            "bug_lane_status": str((bug_lane_state or {}).get("status") or "active"),
            "pending_proposals": len(pending_proposals),
            "panel_sync": panel_sync,
            "report_id": run_id,
        },
    )
    summary = "\n".join([item for item in summaries if item]).strip() or "Team workflow run completed."
    return {
        "ok": True,
        "team_id": normalized_team_id,
        "run_id": run_id,
        "target_id": target_id,
        "repo_root": str(repo_root),
        "repo_locator": repo_locator,
        "project_id": project_id,
        "summary": summary,
        "ci_actions": ci_actions,
        "notes": notes,
        "current_version": current_version,
        "planned_version": planned_version,
        "bug_findings": bug_finding_count,
        "bug_lane_status": str((bug_lane_state or {}).get("status") or "active"),
        "records": records,
        "pending_proposals": pending_proposals,
        "panel_sync": panel_sync,
        "report_path": "",
        "write_delegate": {
            "write_mode": "team_workflow_runtime",
            "writer": "generic_team_runtime",
            "team_id": normalized_team_id,
        },
    }
