from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import crewai_runtime
from . import crew_tools
from . import improvement_store
from . import team_runtime_registry
from .state_store import team_os_root
from . import redis_bus


@dataclass(frozen=True)
class RunSpec:
    project_id: str
    workstream_id: str
    objective: str
    flow: str
    task_id: str = ""
    target_id: str = ""
    repo_path: str = ""
    repo_url: str = ""
    repo_locator: str = ""
    dry_run: bool = False
    force: bool = False
    trigger: str = ""


def _publish_redis_run_event(*, event_type: str, actor: str, spec: RunSpec, payload: dict[str, Any]) -> None:
    # Best-effort only; must never fail run execution.
    try:
        redis_bus.publish_event(
            channel="",
            payload={
                "event_type": event_type,
                "actor": actor,
                "project_id": spec.project_id,
                "workstream_id": spec.workstream_id,
                "payload": payload,
            },
        )
    except Exception:
        pass


def _persist_team_run_logs_best_effort(*, db: Any, run_id: str) -> dict[str, str]:
    target_run_id = str(run_id or "").strip()
    if not target_run_id:
        return {}
    try:
        payload = improvement_store.persist_team_run_logs(db=db, run_id=target_run_id, limit=500)
    except Exception:
        return {}
    saved_logs = payload.get("saved_logs") if isinstance(payload.get("saved_logs"), dict) else {}
    return {
        "json_path": str(saved_logs.get("json_path") or ""),
        "markdown_path": str(saved_logs.get("markdown_path") or ""),
    }


def run_once(*, db, spec: RunSpec, actor: str = "orchestrator") -> dict[str, Any]:
    flow = crew_tools.normalize_flow(spec.flow)
    task_id = str(spec.task_id or "").strip()
    run_id_seed = f"run-{task_id}" if task_id else None
    try:
        crewai_info = crewai_runtime.require_crewai_importable()
    except crewai_runtime.CrewAIRuntimeError as e:
        err_payload = {
            "run_id": "",
            "flow": flow,
            "task_id": task_id,
            "error": str(e),
            "crewai": crewai_runtime.probe_crewai(),
        }
        run_id = db.upsert_run(run_id=run_id_seed, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="FAILED")
        err_payload["run_id"] = run_id
        db.add_event(
            event_type="RUN_FAILED",
            actor=actor,
            project_id=spec.project_id,
            workstream_id=spec.workstream_id,
            payload=err_payload,
        )
        _publish_redis_run_event(
            event_type="RUN_FAILED",
            actor=actor,
            spec=spec,
            payload=err_payload,
        )
        log_paths = _persist_team_run_logs_best_effort(db=db, run_id=run_id) if crew_tools.is_native_crewai_flow(flow) else {}
        return {
            "ok": False,
            "run_id": run_id,
            "flow": flow,
            "task_id": task_id,
            "error": str(e),
            "crewai": err_payload["crewai"],
            "log_paths": log_paths,
            "report_path": str(log_paths.get("markdown_path") or ""),
        }

    if crew_tools.is_native_crewai_flow(flow):
        run_id = db.upsert_run(run_id=run_id_seed, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="RUNNING")
        write_delegate = {
            "write_mode": "crewai_team_runtime",
            "writer": "crewai_agents",
            "truth_sources": ["task_ledger", "github_issues", "github_projects"],
            "team_id": crew_tools.native_team_id(flow),
        }
        db.add_event(
            event_type="RUN_STARTED",
            actor=actor,
            project_id=spec.project_id,
            workstream_id=spec.workstream_id,
            payload={
                "run_id": run_id,
                "flow": flow,
                "task_id": task_id,
                "crewai": crewai_info,
                "write_delegate": write_delegate,
            },
        )
        _publish_redis_run_event(
            event_type="RUN_STARTED",
            actor=actor,
            spec=spec,
            payload={"run_id": run_id, "flow": flow, "task_id": task_id, "crewai": crewai_info, "write_delegate": write_delegate},
        )
        try:
            out = team_runtime_registry.team_runtime_adapter(crew_tools.native_team_id(flow)).run_once_fn(
                db=db,
                spec=spec,
                actor=actor,
                run_id=run_id,
                crewai_info=crewai_info,
            )
        except Exception as e:
            db.update_run_state(run_id=run_id, state="FAILED")
            err_payload = {
                "run_id": run_id,
                "flow": flow,
                "task_id": task_id,
                "error": str(e),
                "crewai": crewai_info,
                "write_delegate": write_delegate,
            }
            db.add_event(
                event_type="RUN_FAILED",
                actor=actor,
                project_id=spec.project_id,
                workstream_id=spec.workstream_id,
                payload=err_payload,
            )
            _publish_redis_run_event(
                event_type="RUN_FAILED",
                actor=actor,
                spec=spec,
                payload=err_payload,
            )
            log_paths = _persist_team_run_logs_best_effort(db=db, run_id=run_id)
            return {
                "ok": False,
                "run_id": run_id,
                "flow": flow,
                "task_id": task_id,
                "error": str(e),
                "crewai": crewai_info,
                "log_paths": log_paths,
                "report_path": str(log_paths.get("markdown_path") or ""),
            }

        db.update_run_state(run_id=run_id, state="DONE" if bool(out.get("ok")) else "FAILED")
        payload = {
            "run_id": run_id,
            "flow": flow,
            "task_id": task_id,
            "crewai": crewai_info,
            "write_delegate": out.get("write_delegate") or write_delegate,
            "summary": str(out.get("summary") or ""),
            "records": list(out.get("records") or []),
            "report_path": str(out.get("report_path") or ""),
            "panel_sync": out.get("panel_sync") or {},
            "skipped": bool(out.get("skipped")),
        }
        db.add_event(
            event_type="RUN_FINISHED" if bool(out.get("ok")) else "RUN_FAILED",
            actor=actor,
            project_id=spec.project_id,
            workstream_id=spec.workstream_id,
            payload=payload,
        )
        _publish_redis_run_event(
            event_type="RUN_FINISHED" if bool(out.get("ok")) else "RUN_FAILED",
            actor=actor,
            spec=spec,
            payload=payload,
        )
        log_paths = _persist_team_run_logs_best_effort(db=db, run_id=run_id)
        report_path = str(out.get("report_path") or log_paths.get("markdown_path") or "")
        return {**out, "run_id": run_id, "flow": flow, "task_id": task_id, "log_paths": log_paths, "report_path": report_path}

    try:
        pipelines = crew_tools.flow_to_pipelines(flow)
    except crew_tools.CrewToolsError as e:
        err_payload = {
            "run_id": "",
            "flow": flow,
            "task_id": task_id,
            "error": str(e),
            "crewai": crewai_info,
            "supported_flows": crew_tools.supported_flows(),
            "direct_pipeline_allowlist": crew_tools.direct_pipeline_allowlist(),
            "write_mode": "delegated_pipeline_script_only",
        }
        run_id = db.upsert_run(run_id=run_id_seed, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="FAILED")
        err_payload["run_id"] = run_id
        db.add_event(
            event_type="RUN_FAILED",
            actor=actor,
            project_id=spec.project_id,
            workstream_id=spec.workstream_id,
            payload=err_payload,
        )
        _publish_redis_run_event(
            event_type="RUN_FAILED",
            actor=actor,
            spec=spec,
            payload=err_payload,
        )
        return {
            "ok": False,
            "run_id": run_id,
            "flow": flow,
            "task_id": task_id,
            "error": str(e),
            "crewai": crewai_info,
            "supported_flows": crew_tools.supported_flows(),
            "direct_pipeline_allowlist": crew_tools.direct_pipeline_allowlist(),
        }

    run_id = db.upsert_run(run_id=run_id_seed, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="RUNNING")
    repo = team_os_root()
    ws_root = crew_tools.workspace_root()
    write_delegate = crew_tools.run_write_evidence(pipelines=pipelines, repo_root=repo)
    db.add_event(
        event_type="RUN_STARTED",
        actor=actor,
        project_id=spec.project_id,
        workstream_id=spec.workstream_id,
        payload={
            "run_id": run_id,
            "flow": flow,
            "task_id": task_id,
            "crewai": crewai_info,
            "pipelines": pipelines,
            "write_delegate": write_delegate,
            "evidence": "truth-source writes are delegated to deterministic pipeline scripts",
        },
    )
    _publish_redis_run_event(
        event_type="RUN_STARTED",
        actor=actor,
        spec=spec,
        payload={
            "run_id": run_id,
            "flow": flow,
            "task_id": task_id,
            "crewai": crewai_info,
            "pipelines": pipelines,
            "write_delegate": write_delegate,
        },
    )

    stages = ["intake", "planning", "execute", "verify", "report"]
    for st in stages:
        db.add_event(
            event_type="RUN_STAGE_CHANGED",
            actor=actor,
            project_id=spec.project_id,
            workstream_id=spec.workstream_id,
            payload={"run_id": run_id, "stage": st, "flow": flow},
        )

    step_results: list[dict[str, Any]] = []
    ok = True
    for pipeline in pipelines:
        step = crew_tools.run_pipeline(pipeline=pipeline, repo_root=repo, workspace_root=ws_root)
        step_ok = int(step.get("returncode", 1)) == 0
        step_summary = {
            "pipeline": pipeline,
            "script_path": str(step.get("script_path") or ""),
            "returncode": int(step.get("returncode", 1)),
            "ok": step_ok,
            "stdout": str(step.get("stdout") or "")[-1000:],
            "stderr": str(step.get("stderr") or "")[-1000:],
            "write_delegate": step.get("write_delegate") or {},
        }
        step_results.append(step_summary)
        db.add_event(
            event_type="RUN_PIPELINE_DELEGATED",
            actor=actor,
            project_id=spec.project_id,
            workstream_id=spec.workstream_id,
            payload={
                "run_id": run_id,
                "flow": flow,
                "pipeline": pipeline,
                "returncode": step_summary["returncode"],
                "ok": step_summary["ok"],
                "write_delegate": step_summary["write_delegate"],
            },
        )
        if not step_ok:
            ok = False
            break

    db.update_run_state(run_id=run_id, state="DONE" if ok else "FAILED")
    db.add_event(
        event_type="RUN_FINISHED" if ok else "RUN_FAILED",
        actor=actor,
        project_id=spec.project_id,
        workstream_id=spec.workstream_id,
        payload={
            "run_id": run_id,
            "flow": flow,
            "task_id": task_id,
            "crewai": crewai_info,
            "pipelines": pipelines,
            "steps": step_results,
            "write_delegate": write_delegate,
        },
    )
    _publish_redis_run_event(
        event_type="RUN_FINISHED" if ok else "RUN_FAILED",
        actor=actor,
        spec=spec,
        payload={
            "run_id": run_id,
            "flow": flow,
            "task_id": task_id,
            "crewai": crewai_info,
            "pipelines": pipelines,
            "steps": step_results,
            "write_delegate": write_delegate,
        },
    )
    last_rc = 0
    if step_results:
        try:
            last_rc = int(step_results[-1].get("returncode") or 0)
        except Exception:
            last_rc = 1
    return {
        "ok": ok,
        "run_id": run_id,
        "flow": flow,
        "task_id": task_id,
        # backward-compatible field (single-step flows)
        "pipeline": step_results[0]["pipeline"] if len(step_results) == 1 else "multi",
        "returncode": last_rc,
        "steps": step_results,
        "crewai": crewai_info,
        "write_delegate": write_delegate,
    }
