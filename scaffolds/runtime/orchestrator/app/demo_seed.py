from __future__ import annotations

from .runtime_db import RuntimeDB


def seed_mock_data(db: RuntimeDB, *, project_id: str, workstream_id: str) -> None:
    """
    Seed minimal demo data so `teamos status` can show agents/runs even without a real executor.
    Safe to call repeatedly; it only seeds when no agents exist.
    """
    if db.list_agents(project_id=project_id):
        return

    pid = str(project_id)
    # Keep deterministic demo ids for easier panel mapping.
    run_id = "demo-run-001" if pid.upper() == "DEMO" else f"{pid}-run-001"
    objective = (
        "DEMO: validate control-plane + CLI + requirements conflict check"
        if pid.upper() == "DEMO"
        else "demo: validate GitHub Projects panel sync (dry-run) + planning overlay"
    )
    db.upsert_run(
        run_id=run_id,
        project_id=pid,
        workstream_id=workstream_id,
        objective=objective,
        state="RUNNING",
    )

    # Agents: DEMO keeps legacy 3-agent seed; demo uses 2 agents bound to one task as required by panel DEMO.
    if pid.lower() == "demo":
        db.register_agent(
            role_id="Release-Ops",
            project_id=pid,
            workstream_id="devops",
            task_id="DEMO-PANEL-0001",
            state="RUNNING",
            current_action="syncing focus/agents/tasks to GitHub Projects (dry-run)",
        )
        db.register_agent(
            role_id="Developer-Backend",
            project_id=pid,
            workstream_id="backend",
            task_id="DEMO-PANEL-0001",
            state="RUNNING",
            current_action="implementing projects-v2 GraphQL sync client and mappings",
        )
        return

    db.register_agent(
        role_id="PM-Intake",
        project_id=pid,
        workstream_id=workstream_id,
        task_id="DEMO-0001",
        state="RUNNING",
        current_action="triaging requirements + confirming decisions",
    )
    db.register_agent(
        role_id="Developer-AI",
        project_id=pid,
        workstream_id="ai",
        task_id="DEMO-0001",
        state="RUNNING",
        current_action="implementing control-plane endpoints and conflict detection",
    )
    db.register_agent(
        role_id="Release-Ops",
        project_id=pid,
        workstream_id="devops",
        task_id="TASK-20260214-175511",
        state="IDLE",
        current_action="observing runtime health and logs",
    )
