from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import crew_tools
from .state_store import team_os_root
from . import redis_bus


@dataclass(frozen=True)
class RunSpec:
    project_id: str
    workstream_id: str
    objective: str
    flow: str


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


def run_once(*, db, spec: RunSpec, actor: str = "orchestrator") -> dict[str, Any]:
    flow = crew_tools.normalize_flow(spec.flow)
    try:
        pipelines = crew_tools.flow_to_pipelines(flow)
    except crew_tools.CrewToolsError as e:
        err_payload = {
            "run_id": "",
            "flow": flow,
            "error": str(e),
            "supported_flows": crew_tools.supported_flows(),
            "direct_pipeline_allowlist": crew_tools.direct_pipeline_allowlist(),
            "write_mode": "delegated_pipeline_script_only",
        }
        run_id = db.upsert_run(run_id=None, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="FAILED")
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
        return {"ok": False, "run_id": run_id, "flow": flow, "error": str(e), "supported_flows": crew_tools.supported_flows(), "direct_pipeline_allowlist": crew_tools.direct_pipeline_allowlist()}

    run_id = db.upsert_run(run_id=None, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="RUNNING")
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
        # backward-compatible field (single-step flows)
        "pipeline": step_results[0]["pipeline"] if len(step_results) == 1 else "multi",
        "returncode": last_rc,
        "steps": step_results,
        "write_delegate": write_delegate,
    }
