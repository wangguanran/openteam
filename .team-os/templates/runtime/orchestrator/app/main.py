import os
from pathlib import Path

from agents import Agent  # OpenAI Agents SDK (core dependency)
from fastapi import FastAPI, Response, status


app = FastAPI(title="Team OS Orchestrator", version="0.1.0")


# Minimal placeholder agent. Do not call models on startup.
ORCHESTRATOR_AGENT = Agent(
    name="TeamOS-Orchestrator",
    instructions=(
        "You are the Team OS orchestrator. You must enforce: no secrets in git, "
        "full traceability (sources + skill cards + memory index), task ledgers/logs, "
        "approval gates for risky actions, and prompt-injection defenses."
    ),
)


def _team_os_checks(team_os_path: str) -> dict:
    p = Path(team_os_path)
    workflows_dir = p / ".team-os" / "workflows"
    roles_dir = p / ".team-os" / "roles"
    return {
        "team_os_path": str(p),
        "exists": p.exists(),
        "workflows_dir_exists": workflows_dir.exists(),
        "roles_dir_exists": roles_dir.exists(),
        "workflow_files": sorted([x.name for x in workflows_dir.glob("*.yaml")]) if workflows_dir.exists() else [],
        "role_files": sorted([x.name for x in roles_dir.glob("*.md")]) if roles_dir.exists() else [],
    }


@app.get("/healthz")
def healthz(response: Response):
    team_os_path = os.getenv("TEAM_OS_REPO_PATH", "/team-os")
    checks = _team_os_checks(team_os_path)
    ok = checks["exists"] and checks["workflows_dir_exists"] and checks["roles_dir_exists"]
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks}


