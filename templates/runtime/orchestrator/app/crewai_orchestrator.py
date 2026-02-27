from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from .state_store import team_os_root
from . import redis_bus


@dataclass(frozen=True)
class RunSpec:
    project_id: str
    workstream_id: str
    objective: str
    flow: str


_FLOW_PIPELINES: dict[str, list[str]] = {
    # CrewAI-first flow aliases. Pipelines remain deterministic execution units.
    "genesis": ["doctor"],
    "standard": ["doctor"],
    "maintenance": ["doctor", "db_migrate"],
    "migration": ["db_migrate"],
}

_ALLOWED_PIPELINES = {"doctor", "db_migrate"}


def _workspace_root() -> Path:
    env_ws = str((os.getenv("TEAMOS_WORKSPACE_ROOT") or "")).strip()
    if env_ws:
        return Path(env_ws).expanduser().resolve()
    return (Path.home() / ".teamos" / "workspace").resolve()


def _normalize_flow(raw: str) -> str:
    return str(raw or "standard").strip().lower()


def _flow_to_pipelines(flow: str) -> list[str]:
    f = _normalize_flow(flow)
    if f in _FLOW_PIPELINES:
        return list(_FLOW_PIPELINES[f])
    # Backward-compatible direct pipeline mode:
    # - flow="doctor"
    # - flow="pipeline:doctor"
    if f.startswith("pipeline:"):
        p = f.split(":", 1)[1].strip()
        if p in _ALLOWED_PIPELINES:
            return [p]
    if f in _ALLOWED_PIPELINES:
        return [f]
    raise ValueError(f"unsupported_flow: {flow}")


def _pipeline_cmd(*, pipeline: str, repo: Path, ws_root: Path) -> list[str]:
    if pipeline == "doctor":
        return [
            sys.executable,
            str(repo / "scripts" / "pipelines" / "doctor.py"),
            "--repo-root",
            str(repo),
            "--workspace-root",
            str(ws_root),
        ]
    if pipeline == "db_migrate":
        return [
            sys.executable,
            str(repo / "scripts" / "pipelines" / "db_migrate.py"),
            "--repo-root",
            str(repo),
            "--workspace-root",
            str(ws_root),
        ]
    raise ValueError(f"unsupported_pipeline: {pipeline}")


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
    flow = _normalize_flow(spec.flow)
    try:
        pipelines = _flow_to_pipelines(flow)
    except ValueError as e:
        run_id = db.upsert_run(run_id=None, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="FAILED")
        db.add_event(
            event_type="RUN_FAILED",
            actor=actor,
            project_id=spec.project_id,
            workstream_id=spec.workstream_id,
            payload={"run_id": run_id, "flow": flow, "error": str(e)},
        )
        _publish_redis_run_event(
            event_type="RUN_FAILED",
            actor=actor,
            spec=spec,
            payload={"run_id": run_id, "flow": flow, "error": str(e)},
        )
        return {"ok": False, "run_id": run_id, "flow": flow, "error": str(e)}

    run_id = db.upsert_run(run_id=None, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="RUNNING")
    db.add_event(
        event_type="RUN_STARTED",
        actor=actor,
        project_id=spec.project_id,
        workstream_id=spec.workstream_id,
        payload={"run_id": run_id, "flow": flow, "pipelines": pipelines},
    )
    _publish_redis_run_event(
        event_type="RUN_STARTED",
        actor=actor,
        spec=spec,
        payload={"run_id": run_id, "flow": flow, "pipelines": pipelines},
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

    repo = team_os_root()
    ws_root = _workspace_root()
    step_results: list[dict[str, Any]] = []
    ok = True
    for pipeline in pipelines:
        cmd = _pipeline_cmd(pipeline=pipeline, repo=repo, ws_root=ws_root)
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        step_ok = p.returncode == 0
        step_results.append(
            {
                "pipeline": pipeline,
                "returncode": p.returncode,
                "ok": step_ok,
                "stdout": (p.stdout or "")[-1000:],
                "stderr": (p.stderr or "")[-1000:],
            }
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
    }
