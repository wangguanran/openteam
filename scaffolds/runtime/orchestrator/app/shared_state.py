"""Shared mutable state used by route modules and the main app."""

import os
import threading
from typing import Any, Optional

from agents import Agent
from team_os_common import utc_now_iso as _utc_now_iso

from . import crewai_role_registry
from .runtime_db import RuntimeDB
from .state_store import runtime_state_root


# ---------------------------------------------------------------------------
# DB singleton
# ---------------------------------------------------------------------------

def _db() -> RuntimeDB:
    db_path = os.getenv("TEAMOS_RUNTIME_DB_PATH")
    if not db_path:
        db_path = str(runtime_state_root() / "runtime.db")
    return RuntimeDB(db_path)


DB = _db()


# ---------------------------------------------------------------------------
# Control plane agent (placeholder; never calls models on startup)
# ---------------------------------------------------------------------------

CONTROL_PLANE_AGENT = Agent(
    name="TeamOS-Control-Plane",
    instructions=(
        "You are the Team OS control plane. Enforce: no secrets in git; "
        "full traceability for web research; task ledger/logging; approval gates; "
        "prompt-injection defenses; requirements conflict detection."
    ),
)


# ---------------------------------------------------------------------------
# Scoped run locks
# ---------------------------------------------------------------------------

class _ScopedRunLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._active: set[str] = set()

    def acquire(self, key: str) -> bool:
        normalized = str(key or "").strip() or "__default__"
        with self._guard:
            if normalized in self._active:
                return False
            self._active.add(normalized)
            return True

    def release(self, key: str) -> None:
        normalized = str(key or "").strip() or "__default__"
        with self._guard:
            self._active.discard(normalized)


TEAM_WORKFLOW_LOCKS = _ScopedRunLocks()
TEAM_CODING_LOCKS = _ScopedRunLocks()


# ---------------------------------------------------------------------------
# Team workflow loop state
# ---------------------------------------------------------------------------

_TEAM_WORKFLOW_LOOP_STATE_LOCK = threading.Lock()
_TEAM_WORKFLOW_LOOP_STATE: dict[str, dict[str, Any]] = {}
TEAM_WORKFLOW_LOOP_CLEANUP = "team_workflow_cleanup"


def set_team_workflow_loop_state(loop_id: str, **fields: Any) -> None:
    now = _utc_now_iso()
    with _TEAM_WORKFLOW_LOOP_STATE_LOCK:
        current = dict(_TEAM_WORKFLOW_LOOP_STATE.get(loop_id) or {})
        current.update(fields)
        current.setdefault("loop_id", loop_id)
        current["updated_at"] = now
        _TEAM_WORKFLOW_LOOP_STATE[loop_id] = current


def team_workflow_loop_state_snapshot() -> dict[str, dict[str, Any]]:
    with _TEAM_WORKFLOW_LOOP_STATE_LOCK:
        return {key: dict(value) for key, value in _TEAM_WORKFLOW_LOOP_STATE.items()}


def team_workflow_stage_loop_state_snapshot() -> dict[str, dict[str, Any]]:
    return team_workflow_loop_state_snapshot()


# ---------------------------------------------------------------------------
# Panel sync scheduling state
# ---------------------------------------------------------------------------

PANEL_DIRTY: set[str] = set()
PANEL_LOCK = threading.Lock()

TEAM_WORKFLOW_STALE_AGENT_ROLES = frozenset(
    {
        crewai_role_registry.ROLE_PRODUCT_MANAGER,
        crewai_role_registry.ROLE_TEST_MANAGER,
        crewai_role_registry.ROLE_TEST_CASE_GAP_AGENT,
        crewai_role_registry.ROLE_ISSUE_DRAFTER,
        crewai_role_registry.ROLE_PLAN_REVIEW_AGENT,
        crewai_role_registry.ROLE_PLAN_QA_AGENT,
        crewai_role_registry.ROLE_MILESTONE_MANAGER,
        crewai_role_registry.ROLE_PROCESS_OPTIMIZATION_ANALYST,
        crewai_role_registry.ROLE_CODE_QUALITY_ANALYST,
        "Scheduler-Agent",
        "Release-Agent",
        "Process-Metrics-Agent",
    }
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TASK_STATE_IN_PROGRESS = frozenset({"doing", "running", "work", "in_progress", "inprogress"})
RUN_STATE_ACTIVE = frozenset({"RUNNING", "PAUSED"})
