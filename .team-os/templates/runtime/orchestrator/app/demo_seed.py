from __future__ import annotations

from .runtime_db import RuntimeDB


def seed_mock_data(db: RuntimeDB, *, project_id: str, workstream_id: str) -> None:
    """
    Seed minimal demo data so `teamos status` can show agents/runs even without a real executor.
    Safe to call repeatedly; it only seeds when no agents exist.
    """
    if db.list_agents(project_id=project_id):
        return

    db.upsert_run(
        run_id="demo-run-001",
        project_id=project_id,
        workstream_id=workstream_id,
        objective="DEMO: validate control-plane + CLI + requirements conflict check",
        state="RUNNING",
    )

    db.register_agent(
        role_id="PM-Intake",
        project_id=project_id,
        workstream_id=workstream_id,
        task_id="DEMO-0001",
        state="RUNNING",
        current_action="triaging requirements + confirming decisions",
    )
    db.register_agent(
        role_id="Developer-AI",
        project_id=project_id,
        workstream_id="ai",
        task_id="DEMO-0001",
        state="RUNNING",
        current_action="implementing control-plane endpoints and conflict detection",
    )
    db.register_agent(
        role_id="Release-Ops",
        project_id=project_id,
        workstream_id="devops",
        task_id="TASK-20260214-175511",
        state="IDLE",
        current_action="observing runtime health and logs",
    )

