from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Optional

from .state_store import team_os_root


@dataclass(frozen=True)
class RunSpec:
    project_id: str
    workstream_id: str
    objective: str
    pipeline: str


def _allowed_pipeline_cmd(spec: RunSpec) -> list[str]:
    repo = team_os_root()
    ws_root = str((repo / ".team-os").resolve())
    if spec.pipeline == "doctor":
        return [sys.executable, str(repo / ".team-os" / "scripts" / "pipelines" / "doctor.py"), "--repo-root", str(repo), "--workspace-root", str((repo / ".." / ".teamos" / "workspace").resolve())]
    if spec.pipeline == "db_migrate":
        return [sys.executable, str(repo / ".team-os" / "scripts" / "pipelines" / "db_migrate.py"), "--repo-root", str(repo), "--workspace-root", str((repo / ".." / ".teamos" / "workspace").resolve())]
    # Safe default: no-op deterministic validation
    return [sys.executable, "-c", "print('orchestrator_noop')"]


def run_once(*, db, spec: RunSpec, actor: str = "orchestrator") -> dict[str, Any]:
    run_id = db.upsert_run(run_id=None, project_id=spec.project_id, workstream_id=spec.workstream_id, objective=spec.objective, state="RUNNING")
    db.add_event(event_type="RUN_STARTED", actor=actor, project_id=spec.project_id, workstream_id=spec.workstream_id, payload={"run_id": run_id, "pipeline": spec.pipeline})

    stages = ["intake", "planning", "execute", "verify", "report"]
    for st in stages:
        db.add_event(event_type="RUN_STAGE_CHANGED", actor=actor, project_id=spec.project_id, workstream_id=spec.workstream_id, payload={"run_id": run_id, "stage": st})

    cmd = _allowed_pipeline_cmd(spec)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    ok = p.returncode == 0

    db.update_run_state(run_id=run_id, state="DONE" if ok else "FAILED")
    db.add_event(
        event_type="RUN_FINISHED" if ok else "RUN_FAILED",
        actor=actor,
        project_id=spec.project_id,
        workstream_id=spec.workstream_id,
        payload={
            "run_id": run_id,
            "pipeline": spec.pipeline,
            "returncode": p.returncode,
            "stdout": (p.stdout or "")[-1000:],
            "stderr": (p.stderr or "")[-1000:],
        },
    )
    return {
        "ok": ok,
        "run_id": run_id,
        "pipeline": spec.pipeline,
        "returncode": p.returncode,
    }
