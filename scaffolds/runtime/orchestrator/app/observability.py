"""
Observability module for OpenTeam Control Plane.

Provides real-time metrics aggregation, workflow health monitoring,
cost tracking, and local alert generation.
"""
from __future__ import annotations

import json
import os
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .runtime_db import EventRow, RunRow, AgentRow


# ---------------------------------------------------------------------------
# Metrics Store (in-memory, thread-safe)
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()


@dataclass
class _RunMetrics:
    run_id: str
    project_id: str
    workflow_id: str
    started_at: float
    finished_at: float = 0.0
    state: str = "RUNNING"
    token_usage: int = 0
    estimated_cost_usd: float = 0.0
    error_count: int = 0
    task_count: int = 0


@dataclass
class _MetricsSnapshot:
    """Thread-safe, in-memory metrics accumulator."""

    # Counters
    total_runs: int = 0
    total_events: int = 0
    active_runs: int = 0
    failed_runs: int = 0
    completed_runs: int = 0

    # Per-workflow counters
    runs_by_workflow: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errors_by_workflow: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Cost tracking
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    cost_by_project: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    # Latency (per-workflow, seconds)
    durations_by_workflow: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    # Active run details
    run_details: dict[str, _RunMetrics] = field(default_factory=dict)

    # Alerts
    alerts: list[dict[str, Any]] = field(default_factory=list)

    # Boot time
    boot_ts: float = field(default_factory=time.time)


_METRICS = _MetricsSnapshot()

# Cost rates per 1K tokens (conservative estimates; configurable via env)
_COST_PER_1K_INPUT = float(os.getenv("OPENTEAM_LLM_COST_PER_1K_INPUT", "0.003"))
_COST_PER_1K_OUTPUT = float(os.getenv("OPENTEAM_LLM_COST_PER_1K_OUTPUT", "0.015"))
_MAX_ALERTS = 500


# ---------------------------------------------------------------------------
# Recording helpers (called from main.py / orchestrator)
# ---------------------------------------------------------------------------

def record_run_start(
    *,
    run_id: str,
    project_id: str,
    workflow_id: str,
) -> None:
    with _LOCK:
        _METRICS.total_runs += 1
        _METRICS.active_runs += 1
        _METRICS.runs_by_workflow[workflow_id] += 1
        _METRICS.run_details[run_id] = _RunMetrics(
            run_id=run_id,
            project_id=project_id,
            workflow_id=workflow_id,
            started_at=time.time(),
        )


def record_run_end(
    *,
    run_id: str,
    state: str,
    token_usage: int = 0,
) -> None:
    with _LOCK:
        _METRICS.active_runs = max(0, _METRICS.active_runs - 1)
        rm = _METRICS.run_details.get(run_id)
        if rm:
            rm.finished_at = time.time()
            rm.state = state
            rm.token_usage = token_usage
            duration = rm.finished_at - rm.started_at
            _METRICS.durations_by_workflow[rm.workflow_id].append(duration)

            # Cost estimation
            estimated = (token_usage / 1000.0) * ((_COST_PER_1K_INPUT + _COST_PER_1K_OUTPUT) / 2)
            rm.estimated_cost_usd = estimated
            _METRICS.total_tokens += token_usage
            _METRICS.total_cost_usd += estimated
            _METRICS.cost_by_project[rm.project_id] += estimated

        if state in ("COMPLETED", "DONE", "SUCCESS"):
            _METRICS.completed_runs += 1
        elif state in ("FAILED", "ERROR"):
            _METRICS.failed_runs += 1
            if rm:
                _METRICS.errors_by_workflow[rm.workflow_id] += 1
                _add_alert("run_failed", f"Run {run_id} failed (workflow={rm.workflow_id})", {
                    "run_id": run_id,
                    "workflow_id": rm.workflow_id,
                    "project_id": rm.project_id,
                })


def record_event(*, event_type: str, project_id: str) -> None:
    with _LOCK:
        _METRICS.total_events += 1


def record_token_usage(*, run_id: str, tokens: int) -> None:
    with _LOCK:
        rm = _METRICS.run_details.get(run_id)
        if rm:
            rm.token_usage += tokens


def _add_alert(kind: str, message: str, details: dict[str, Any]) -> None:
    """Must be called under _LOCK."""
    alert = {
        "kind": kind,
        "message": message,
        "details": details,
        "ts": time.time(),
    }
    _METRICS.alerts.append(alert)
    if len(_METRICS.alerts) > _MAX_ALERTS:
        _METRICS.alerts = _METRICS.alerts[-_MAX_ALERTS:]


def add_alert(kind: str, message: str, details: Optional[dict[str, Any]] = None) -> None:
    with _LOCK:
        _add_alert(kind, message, details or {})


# ---------------------------------------------------------------------------
# Snapshot / query helpers (called from API endpoints)
# ---------------------------------------------------------------------------

def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[f]
    return s[f] + (k - f) * (s[c] - s[f])


def get_metrics_summary() -> dict[str, Any]:
    with _LOCK:
        uptime = time.time() - _METRICS.boot_ts

        # Per-workflow latency stats
        workflow_latency: dict[str, dict[str, float]] = {}
        for wf, durs in _METRICS.durations_by_workflow.items():
            if durs:
                workflow_latency[wf] = {
                    "count": len(durs),
                    "p50_sec": round(_percentile(durs, 0.5), 2),
                    "p95_sec": round(_percentile(durs, 0.95), 2),
                    "p99_sec": round(_percentile(durs, 0.99), 2),
                    "avg_sec": round(sum(durs) / len(durs), 2),
                    "max_sec": round(max(durs), 2),
                }

        return {
            "uptime_sec": round(uptime, 1),
            "total_runs": _METRICS.total_runs,
            "active_runs": _METRICS.active_runs,
            "completed_runs": _METRICS.completed_runs,
            "failed_runs": _METRICS.failed_runs,
            "total_events": _METRICS.total_events,
            "runs_by_workflow": dict(_METRICS.runs_by_workflow),
            "errors_by_workflow": dict(_METRICS.errors_by_workflow),
            "workflow_latency": workflow_latency,
            "cost": {
                "total_tokens": _METRICS.total_tokens,
                "total_cost_usd": round(_METRICS.total_cost_usd, 4),
                "cost_by_project": {k: round(v, 4) for k, v in _METRICS.cost_by_project.items()},
            },
        }


def get_active_runs() -> list[dict[str, Any]]:
    with _LOCK:
        now = time.time()
        active = []
        for rm in _METRICS.run_details.values():
            if rm.finished_at > 0:
                continue
            active.append({
                "run_id": rm.run_id,
                "project_id": rm.project_id,
                "workflow_id": rm.workflow_id,
                "elapsed_sec": round(now - rm.started_at, 1),
                "token_usage": rm.token_usage,
                "task_count": rm.task_count,
            })
        return active


def get_recent_alerts(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        return list(reversed(_METRICS.alerts[-limit:]))


def get_health_report(
    *,
    db_runs: list[RunRow],
    db_agents: list[AgentRow],
    db_events: list[EventRow],
) -> dict[str, Any]:
    """Generate a comprehensive health report combining DB state with in-memory metrics."""
    metrics = get_metrics_summary()

    active_agents = [a for a in db_agents if a.state in ("active", "running", "working")]
    stale_agents = [a for a in db_agents if a.state in ("stale", "error", "lost")]

    running_db_runs = [r for r in db_runs if r.state in ("RUNNING", "PAUSED")]

    recent_events = db_events[-100:] if db_events else []
    event_types: dict[str, int] = defaultdict(int)
    for e in recent_events:
        event_types[e.event_type] += 1

    success_rate = 0.0
    total = metrics["completed_runs"] + metrics["failed_runs"]
    if total > 0:
        success_rate = round(metrics["completed_runs"] / total * 100, 1)

    return {
        "status": "healthy" if metrics["failed_runs"] == 0 and not stale_agents else "degraded",
        "metrics": metrics,
        "agents": {
            "total": len(db_agents),
            "active": len(active_agents),
            "stale": len(stale_agents),
            "stale_details": [{"agent_id": a.agent_id, "role_id": a.role_id, "state": a.state} for a in stale_agents[:10]],
        },
        "runs": {
            "db_running": len(running_db_runs),
            "in_memory_active": metrics["active_runs"],
        },
        "success_rate_pct": success_rate,
        "recent_event_types": dict(sorted(event_types.items(), key=lambda kv: -kv[1])[:20]),
        "alerts": get_recent_alerts(20),
    }


# ---------------------------------------------------------------------------
# Alert checking (can be called periodically)
# ---------------------------------------------------------------------------

_ALERT_THRESHOLDS = {
    "max_active_runs": int(os.getenv("OPENTEAM_ALERT_MAX_ACTIVE_RUNS", "20")),
    "max_error_rate_pct": float(os.getenv("OPENTEAM_ALERT_MAX_ERROR_RATE", "30")),
    "max_run_duration_sec": int(os.getenv("OPENTEAM_ALERT_MAX_RUN_DURATION", "3600")),
}


def check_alerts() -> list[dict[str, Any]]:
    """Check current state against thresholds and emit alerts."""
    new_alerts: list[dict[str, Any]] = []

    with _LOCK:
        # High active run count
        if _METRICS.active_runs > _ALERT_THRESHOLDS["max_active_runs"]:
            alert = {
                "kind": "high_concurrency",
                "message": f"Active runs ({_METRICS.active_runs}) exceed threshold ({_ALERT_THRESHOLDS['max_active_runs']})",
                "details": {"active_runs": _METRICS.active_runs, "threshold": _ALERT_THRESHOLDS["max_active_runs"]},
            }
            _add_alert(**alert)
            new_alerts.append(alert)

        # High error rate
        total = _METRICS.completed_runs + _METRICS.failed_runs
        if total > 5:
            error_rate = (_METRICS.failed_runs / total) * 100
            if error_rate > _ALERT_THRESHOLDS["max_error_rate_pct"]:
                alert = {
                    "kind": "high_error_rate",
                    "message": f"Error rate ({error_rate:.1f}%) exceeds threshold ({_ALERT_THRESHOLDS['max_error_rate_pct']}%)",
                    "details": {"error_rate_pct": round(error_rate, 1), "failed": _METRICS.failed_runs, "total": total},
                }
                _add_alert(**alert)
                new_alerts.append(alert)

        # Long-running runs
        now = time.time()
        for rm in _METRICS.run_details.values():
            if rm.finished_at > 0:
                continue
            elapsed = now - rm.started_at
            if elapsed > _ALERT_THRESHOLDS["max_run_duration_sec"]:
                alert = {
                    "kind": "long_running",
                    "message": f"Run {rm.run_id} running for {elapsed:.0f}s (threshold: {_ALERT_THRESHOLDS['max_run_duration_sec']}s)",
                    "details": {"run_id": rm.run_id, "elapsed_sec": round(elapsed), "workflow_id": rm.workflow_id},
                }
                _add_alert(**alert)
                new_alerts.append(alert)

    return new_alerts
