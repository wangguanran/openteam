from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import team_registry
from app import team_workflow_runtime
from app.github_issues_bus import list_issue_comments, upsert_comment_with_marker
from app.panel_github_sync import GitHubProjectsPanelSync
from app.skill_library.executor import register_skill


def _team_id_from_context(context: Any) -> str:
    workflow = getattr(context, "workflow", None)
    team_id = str(getattr(workflow, "team_id", "") or "").strip()
    return team_id or team_registry.default_team_id()


def _safe_target_from_context(context: Any) -> dict[str, Any]:
    project_id = team_workflow_runtime.safe_project_id(str(context.project_id or "openteam"))
    return team_workflow_runtime.resolve_target(
        target_id=str(context.target_id or "").strip(),
        repo_path=str((context.extra or {}).get("repo_path") or "").strip(),
        repo_url=str((context.extra or {}).get("repo_url") or "").strip(),
        repo_locator=str((context.extra or {}).get("repo_locator") or "").strip(),
        project_id=project_id,
    )


@register_skill("team.delivery-studio.noop")
def delivery_studio_noop_skill(*, context: Any, inputs: dict[str, Any], state: dict[str, Any], spec: Any) -> dict[str, Any]:
    _ = state
    team_id = _team_id_from_context(context)
    return {
        "ok": True,
        "outputs": {
            "handled": True,
            "team_id": team_id,
            "skill_id": str(getattr(spec, "skill_id", "") or "").strip(),
            "handler_id": str(getattr(spec, "handler_id", "") or "").strip(),
            "inputs": dict(inputs or {}),
        },
    }


def _repo_context_outputs(*, target: dict[str, Any], workflow: Any, force: bool) -> dict[str, Any]:
    project_id = team_workflow_runtime.safe_project_id(str(target.get("project_id") or "openteam"))
    target_id = str(target.get("target_id") or "").strip() or "openteam"
    repo_root = Path(str(target.get("repo_root") or team_workflow_runtime.openteam_root())).expanduser().resolve()
    scan_repo_root = team_workflow_runtime.prepare_discovery_repo(source_repo_root=repo_root, target=target)
    repo_context = team_workflow_runtime.collect_repo_context(
        repo_root=repo_root,
        scan_repo_root=scan_repo_root,
        explicit_repo_locator=str(target.get("repo_locator") or ""),
        target_id=target_id,
    )
    repo_locator = str(repo_context.get("repo_locator") or target.get("repo_locator") or "").strip()
    should_skip, skip_reason = team_workflow_runtime.should_skip(target_id=target_id, repo_root=repo_root, force=force)
    bug_scan_policy = {}
    if str(workflow.lane or "").strip().lower() == "bug":
        bug_scan_policy = team_workflow_runtime.bug_scan_policy(
            target_id=target_id,
            project_id=project_id,
            repo_context=repo_context,
            force=force,
        )
    return {
        "project_id": project_id,
        "target": target,
        "target_id": target_id,
        "repo_root": str(repo_root),
        "scan_repo_root": str(scan_repo_root),
        "repo_locator": repo_locator,
        "repo_context": repo_context,
        "repo_context_blob": json.dumps(team_workflow_runtime.prompt_safe_repo_context(repo_context), ensure_ascii=False, indent=2),
        "current_version": str(repo_context.get("current_version") or "0.1.0").strip() or "0.1.0",
        "max_findings": int(workflow.max_candidates()),
        "bug_scan_dormant": bool(bug_scan_policy.get("dormant")),
        "bug_scan_policy": bug_scan_policy,
        "skip": bool(should_skip),
        "skip_reason": str(skip_reason or "").strip(),
    }


@register_skill("team.collect-context")
def collect_repo_context_skill(*, context: Any, inputs: dict[str, Any], state: dict[str, Any], spec: Any) -> dict[str, Any]:
    _ = inputs
    _ = state
    _ = spec
    target = _safe_target_from_context(context)
    outputs = _repo_context_outputs(target=target, workflow=context.workflow, force=bool(context.force))
    if bool(outputs.get("skip")):
        return {
            "ok": True,
            "outputs": outputs,
            "control": {"stop": True, "reason": str(outputs.get("skip_reason") or "workflow_skip")},
        }
    return {"ok": True, "outputs": outputs}


@register_skill("team.materialize-findings")
def materialize_findings_skill(*, context: Any, inputs: dict[str, Any], state: dict[str, Any], spec: Any) -> dict[str, Any]:
    _ = state
    _ = spec
    from app.workflow_models import UpgradePlan

    team_id = _team_id_from_context(context)
    raw_plan = inputs.get("plan") or inputs.get("plan_json") or {}
    plan = UpgradePlan.model_validate(raw_plan)
    workflow = context.workflow
    project_id = team_workflow_runtime.safe_project_id(str(inputs.get("project_id") or context.project_id or "openteam"))
    target_id = str(inputs.get("target_id") or context.target_id or "").strip() or "openteam"
    repo_root = Path(str(inputs.get("repo_root") or team_workflow_runtime.openteam_root())).expanduser().resolve()
    repo_locator = str(inputs.get("repo_locator") or "").strip()
    current_version = str(inputs.get("current_version") or plan.current_version or "0.1.0").strip() or "0.1.0"
    dry_run = bool(context.dry_run)
    records: list[dict[str, Any]] = []
    pending_proposals: list[dict[str, Any]] = []
    workflow_lane = str(workflow.lane or "").strip().lower()
    if workflow_lane in ("review", "shared", ""):
        # Unified review workflow: accept all findings regardless of lane
        filtered_findings = list(plan.findings or [])
    else:
        filtered_findings = [
            finding
            for finding in list(plan.findings or [])
            if str(finding.lane or "").strip().lower() == workflow_lane
        ]
    filtered_plan = plan.model_copy(
        update={
            "findings": filtered_findings,
            "planned_version": plan.planned_version or team_workflow_runtime.planned_version(current_version, filtered_findings),
        }
    )
    for finding in filtered_findings:
        work_items = list(finding.work_items or []) or team_workflow_runtime.default_work_items(repo_root=repo_root, finding=finding)
        if not workflow.uses_proposal:
            for work_item in work_items:
                records.append(
                    team_workflow_runtime.record_from_materialized_item(
                        team_id=team_id,
                        target_id=target_id,
                        repo_root=repo_root,
                        repo_locator=repo_locator,
                        project_id=project_id,
                        finding=finding,
                        work_item=work_item,
                        proposal_id="",
                        dry_run=dry_run,
                    )
                )
            continue
        proposal = team_workflow_runtime.upsert_proposal(
            team_id=team_id,
            target_id=target_id,
            repo_root=repo_root,
            repo_locator=repo_locator,
            project_id=project_id,
            finding=finding,
            current_version=current_version,
        )
        if workflow.requires_user_confirmation or bool(finding.requires_user_confirmation):
            proposal = team_workflow_runtime.ensure_proposal_discussion_issue(proposal)
        proposal_id = str(proposal.get("proposal_id") or "")
        status = str(proposal.get("status") or "").strip().upper()
        due = team_workflow_runtime.proposal_due(proposal)
        if workflow.should_materialize(status=status, due=due):
            for work_item in work_items:
                records.append(
                    team_workflow_runtime.record_from_materialized_item(
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
                )
            if not dry_run:
                team_workflow_runtime.mark_proposal_materialized(proposal_id)
            continue
        pending_proposals.append(
            {
                "proposal_id": proposal_id,
                "workflow_id": str(proposal.get("workflow_id") or workflow.workflow_id),
                "lane": finding.lane,
                "title": proposal.get("title") or finding.title,
                "status": status,
                "cooldown_until": proposal.get("cooldown_until") or "",
                "version_bump": proposal.get("version_bump") or finding.version_bump,
                "target_version": proposal.get("target_version") or finding.target_version,
                "requires_user_confirmation": bool(proposal.get("requires_user_confirmation")),
                "discussion_issue_url": proposal.get("discussion_issue_url") or "",
                "discussion_issue_number": int(proposal.get("discussion_issue_number") or 0),
            }
        )
    if dry_run:
        try:
            panel_sync = GitHubProjectsPanelSync(db=context.db).sync(project_id=project_id, mode="full", dry_run=True)
        except Exception as exc:
            panel_sync = {"ok": False, "dry_run": True, "error": str(exc)[:500], "project_id": project_id}
    else:
        panel_sync = team_workflow_runtime.sync_panel(db=context.db, project_id=project_id)
    report_fragment = {
        "summary": filtered_plan.summary,
        "current_version": current_version,
        "planned_version": filtered_plan.planned_version or team_workflow_runtime.planned_version(current_version, filtered_plan.findings),
        "plan": filtered_plan.model_dump(),
        "records": records,
        "pending_proposals": pending_proposals,
        "panel_sync": panel_sync,
        "target_id": target_id,
        "repo_root": str(repo_root),
        "repo_locator": repo_locator,
        "project_id": project_id,
        "bug_scan_policy": inputs.get("bug_scan_policy") or {},
        "team_id": team_id,
    }
    return {"ok": True, "outputs": report_fragment}


@register_skill("team.claim-discussion")
def claim_issue_discussion_skill(*, context: Any, inputs: dict[str, Any], state: dict[str, Any], spec: Any) -> dict[str, Any]:
    _ = inputs
    _ = state
    _ = spec
    team_id = _team_id_from_context(context)
    lane = str(context.workflow.lane or "feature").strip().lower() or "feature"
    proposals = team_workflow_runtime.list_proposals(
        team_id=team_id,
        target_id=str(context.target_id or "").strip(),
        project_id=str(context.project_id or "").strip(),
        lane=lane,
    )
    for proposal in proposals:
        status = str(proposal.get("status") or "").strip().upper()
        if status in ("REJECTED", "MATERIALIZED"):
            continue
        proposal = team_workflow_runtime.ensure_proposal_discussion_issue(proposal)
        issue_number = team_workflow_runtime.discussion_issue_number(proposal)
        repo_locator = str(proposal.get("repo_locator") or "").strip()
        if issue_number <= 0 or not repo_locator:
            continue
        last_seen = int(proposal.get("discussion_last_comment_id") or 0)
        comments = list_issue_comments(repo_locator, issue_number)
        new_comments = [
            comment
            for comment in comments
            if int(getattr(comment, "id", 0) or 0) > last_seen and team_workflow_runtime.is_user_comment(comment)
        ]
        if not new_comments:
            continue
        latest_comment_id = max(int(getattr(comment, "id", 0) or 0) for comment in new_comments)
        comments_text = "\n\n".join(
            [str(getattr(comment, "body", "") or "").strip() for comment in new_comments if str(getattr(comment, "body", "") or "").strip()]
        )
        return {
            "ok": True,
            "outputs": {
                "proposal": proposal,
                "comments": [
                    {
                        "id": int(getattr(comment, "id", 0) or 0),
                        "user_login": str(getattr(comment, "user_login", "") or ""),
                        "body": str(getattr(comment, "body", "") or ""),
                        "created_at": str(getattr(comment, "created_at", "") or ""),
                    }
                    for comment in new_comments
                ],
                "comments_text": comments_text,
                "explicit_action": team_workflow_runtime.proposal_action_from_comment_text(comments_text),
                "latest_comment_id": latest_comment_id,
            },
        }
    return {
        "ok": True,
        "outputs": {"proposal": None},
        "control": {"stop": True, "reason": f"no_pending_{lane}_discussion"},
    }


@register_skill("team.apply-discussion")
def apply_issue_discussion_skill(*, context: Any, inputs: dict[str, Any], state: dict[str, Any], spec: Any) -> dict[str, Any]:
    _ = state
    _ = spec
    from app.workflow_models import ProposalDiscussionResponse

    team_id = _team_id_from_context(context)
    proposal = dict(inputs.get("proposal") or {})
    if not proposal:
        return {"ok": True, "outputs": {"updated": False}}
    response = ProposalDiscussionResponse.model_validate(inputs.get("response") or inputs.get("response_json") or {})
    latest_comment_id = int(inputs.get("latest_comment_id") or 0)
    explicit_action = str(inputs.get("explicit_action") or "").strip().lower()
    comments_text = str(inputs.get("comments_text") or "").strip()
    action = explicit_action or str(response.action or "").strip().lower()
    if action in ("approve", "reject", "hold"):
        proposal = team_workflow_runtime.decide_proposal(
            team_id=team_id,
            proposal_id=str(proposal.get("proposal_id") or ""),
            action=action,
            title=str(response.title or "").strip(),
            summary=str(response.summary or "").strip(),
            version_bump=str(response.version_bump or "").strip(),
        )
        if str(response.module or "").strip():
            proposal = team_workflow_runtime.update_proposal_record(
                str(proposal.get("proposal_id") or ""),
                extra={"module": str(response.module or "").strip()},
            )
    else:
        proposal = team_workflow_runtime.update_proposal_record(
            str(proposal.get("proposal_id") or ""),
            title=str(response.title or "").strip(),
            summary=str(response.summary or "").strip(),
            version_bump=str(response.version_bump or "").strip(),
            extra={"status": "PENDING_CONFIRMATION", "module": str(response.module or "").strip()},
        )
    proposal = team_workflow_runtime.ensure_proposal_discussion_issue(proposal)
    marker = f"<!-- openteam:proposal-reply:{str(proposal.get('proposal_id') or '').strip()}:{latest_comment_id} -->"
    issue_number = team_workflow_runtime.discussion_issue_number(proposal)
    repo_locator = str(proposal.get("repo_locator") or "").strip()
    if issue_number > 0 and repo_locator:
        upsert_comment_with_marker(
            repo_locator,
            issue_number,
            marker=marker,
            body=f"{marker}\n{str(response.reply_body or '').strip()}",
            allow_create=True,
        )
        proposal = team_workflow_runtime.update_proposal_record(
            str(proposal.get("proposal_id") or ""),
            extra={
                "discussion_last_comment_id": latest_comment_id,
                "discussion_last_synced_at": team_workflow_runtime.utc_now_iso(),
                "discussion_last_comment_body": comments_text,
            },
        )
    return {"ok": True, "outputs": {"proposal": proposal, "updated": True, "action": action}}


@register_skill("team.run-coding-pipeline")
def run_delivery_pipeline_skill(*, context: Any, inputs: dict[str, Any], state: dict[str, Any], spec: Any) -> dict[str, Any]:
    _ = inputs
    _ = state
    _ = spec
    team_id = _team_id_from_context(context)
    lane = str(context.workflow.lane or "bug").strip().lower() or "bug"
    requested_task_id = str(context.task_id or "").strip()
    tasks = team_workflow_runtime.list_delivery_tasks(
        team_id=team_id,
        project_id=str(context.project_id or "").strip(),
        target_id=str(context.target_id or "").strip(),
    )
    filtered: list[dict[str, Any]] = []
    for task in tasks:
        if requested_task_id and str(task.get("task_id") or "") != requested_task_id:
            continue
        if (not requested_task_id) and str(task.get("status") or "") not in ("todo", "doing", "test", "release", "merge_conflict"):
            continue
        ledger_path = Path(str(task.get("ledger_path") or "")).expanduser().resolve()
        if not ledger_path.exists():
            continue
        doc = team_workflow_runtime.load_yaml(ledger_path)
        if str(team_workflow_runtime.task_lane(doc) or "bug").strip().lower() != lane:
            continue
        if str(doc.get("team_id") or "").strip() and str(doc.get("team_id") or "").strip() != team_id:
            continue
        filtered.append({**task, "ledger_path": str(ledger_path)})
    if not filtered:
        return {
            "ok": True,
            "outputs": {
                "scanned": 0,
                "processed": 0,
                "tasks": [],
                "summary": team_workflow_runtime.delivery_summary(
                    team_id=team_id,
                    project_id=str(context.project_id or "").strip(),
                    target_id=str(context.target_id or "").strip(),
                ),
            },
            "control": {"stop": True, "reason": f"no_{lane}_delivery_tasks"},
        }
    limit = max(1, int(context.workflow.loop.max_units_per_tick or 1))
    out: list[dict[str, Any]] = []
    processed = 0
    for task in filtered[:limit]:
        lease = team_workflow_runtime.claim_delivery_task_lease(db=context.db, actor=context.actor, task=task)
        if lease is None:
            out.append({"ok": True, "task_id": str(task.get("task_id") or ""), "skipped": True, "reason": "lease_held_by_other"})
            continue
        result = team_workflow_runtime.execute_delivery_candidate(
            db=context.db,
            actor=context.actor,
            ledger_path=Path(str(task.get("ledger_path") or "")).expanduser().resolve(),
            dry_run=bool(context.dry_run),
            force=bool(context.force),
            lease=lease,
        )
        out.append(result)
        if not result.get("skipped"):
            processed += 1
        if requested_task_id:
            break
    return {
        "ok": all(bool(item.get("ok")) or bool(item.get("skipped")) for item in out),
        "outputs": {
            "scanned": len(filtered),
            "processed": processed,
            "tasks": out,
            "summary": team_workflow_runtime.delivery_summary(
                team_id=team_id,
                project_id=str(context.project_id or "").strip(),
                target_id=str(context.target_id or "").strip(),
            ),
        },
    }
