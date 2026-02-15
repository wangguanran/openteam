import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AgentRow:
    agent_id: str
    role_id: str
    project_id: str
    workstream_id: str
    task_id: str
    state: str
    started_at: str
    last_heartbeat: str
    current_action: str


@dataclass(frozen=True)
class RunRow:
    run_id: str
    project_id: str
    workstream_id: str
    objective: str
    state: str
    started_at: str
    last_update: str


@dataclass(frozen=True)
class EventRow:
    id: int
    ts: str
    event_type: str
    actor: str
    project_id: str
    workstream_id: str
    payload: dict[str, Any]


class RuntimeDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                  agent_id TEXT PRIMARY KEY,
                  role_id TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  workstream_id TEXT NOT NULL,
                  task_id TEXT NOT NULL,
                  state TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  last_heartbeat TEXT NOT NULL,
                  current_action TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                  run_id TEXT PRIMARY KEY,
                  project_id TEXT NOT NULL,
                  workstream_id TEXT NOT NULL,
                  objective TEXT NOT NULL,
                  state TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  last_update TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  workstream_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """
            )

            # Panel sync runs (GitHub Projects is a view-layer; keep minimal health metadata here).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS panel_sync_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts_start TEXT NOT NULL,
                  ts_end TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  panel_type TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  dry_run INTEGER NOT NULL,
                  ok INTEGER NOT NULL,
                  stats_json TEXT NOT NULL,
                  error TEXT NOT NULL
                )
                """
            )

            # Simple KV store for small caches (e.g., resolved GitHub field ids).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS panel_kv (
                  key TEXT PRIMARY KEY,
                  value_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    # --- Agent Registry (required) ---
    def register_agent(
        self,
        *,
        role_id: str,
        project_id: str,
        workstream_id: str,
        task_id: str = "",
        state: str = "IDLE",
        current_action: str = "",
    ) -> str:
        agent_id = str(uuid.uuid4())
        now = utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO agents (agent_id, role_id, project_id, workstream_id, task_id, state, started_at, last_heartbeat, current_action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (agent_id, role_id, project_id, workstream_id, task_id, state, now, now, current_action),
            )
            conn.commit()
            return agent_id
        finally:
            conn.close()

    def heartbeat(self, *, agent_id: str, state: Optional[str] = None, current_action: Optional[str] = None) -> None:
        now = utc_now_iso()
        sets = ["last_heartbeat = ?"]
        params: list[Any] = [now]
        if state is not None:
            sets.append("state = ?")
            params.append(state)
        if current_action is not None:
            sets.append("current_action = ?")
            params.append(current_action)
        params.append(agent_id)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE agent_id = ?", params)
            conn.commit()
        finally:
            conn.close()

    def update_assignment(
        self,
        *,
        agent_id: str,
        project_id: Optional[str] = None,
        workstream_id: Optional[str] = None,
        task_id: Optional[str] = None,
        state: Optional[str] = None,
        current_action: Optional[str] = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            sets.append("project_id = ?")
            params.append(project_id)
        if workstream_id is not None:
            sets.append("workstream_id = ?")
            params.append(workstream_id)
        if task_id is not None:
            sets.append("task_id = ?")
            params.append(task_id)
        if state is not None:
            sets.append("state = ?")
            params.append(state)
        if current_action is not None:
            sets.append("current_action = ?")
            params.append(current_action)
        sets.append("last_heartbeat = ?")
        params.append(utc_now_iso())
        params.append(agent_id)
        if not sets:
            return
        conn = self._connect()
        try:
            conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE agent_id = ?", params)
            conn.commit()
        finally:
            conn.close()

    def list_agents(
        self,
        *,
        project_id: Optional[str] = None,
        workstream_id: Optional[str] = None,
        state: Optional[str] = None,
        role_id: Optional[str] = None,
    ) -> list[AgentRow]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if workstream_id:
            clauses.append("workstream_id = ?")
            params.append(workstream_id)
        if state:
            clauses.append("state = ?")
            params.append(state)
        if role_id:
            clauses.append("role_id = ?")
            params.append(role_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT * FROM agents {where} ORDER BY started_at ASC", params).fetchall()
            return [
                AgentRow(
                    agent_id=r["agent_id"],
                    role_id=r["role_id"],
                    project_id=r["project_id"],
                    workstream_id=r["workstream_id"],
                    task_id=r["task_id"],
                    state=r["state"],
                    started_at=r["started_at"],
                    last_heartbeat=r["last_heartbeat"],
                    current_action=r["current_action"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    # --- Runs ---
    def upsert_run(
        self,
        *,
        run_id: Optional[str],
        project_id: str,
        workstream_id: str,
        objective: str,
        state: str,
    ) -> str:
        rid = run_id or str(uuid.uuid4())
        now = utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO runs (run_id, project_id, workstream_id, objective, state, started_at, last_update)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  project_id=excluded.project_id,
                  workstream_id=excluded.workstream_id,
                  objective=excluded.objective,
                  state=excluded.state,
                  last_update=excluded.last_update
                """,
                (rid, project_id, workstream_id, objective, state, now, now),
            )
            conn.commit()
            return rid
        finally:
            conn.close()

    def list_runs(self, *, project_id: Optional[str] = None, workstream_id: Optional[str] = None) -> list[RunRow]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if workstream_id:
            clauses.append("workstream_id = ?")
            params.append(workstream_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT * FROM runs {where} ORDER BY started_at DESC", params).fetchall()
            return [
                RunRow(
                    run_id=r["run_id"],
                    project_id=r["project_id"],
                    workstream_id=r["workstream_id"],
                    objective=r["objective"],
                    state=r["state"],
                    started_at=r["started_at"],
                    last_update=r["last_update"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def get_run(self, run_id: str) -> Optional[RunRow]:
        conn = self._connect()
        try:
            r = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if not r:
                return None
            return RunRow(
                run_id=r["run_id"],
                project_id=r["project_id"],
                workstream_id=r["workstream_id"],
                objective=r["objective"],
                state=r["state"],
                started_at=r["started_at"],
                last_update=r["last_update"],
            )
        finally:
            conn.close()

    def update_run_state(self, *, run_id: str, state: str) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE runs SET state = ?, last_update = ? WHERE run_id = ?", (state, utc_now_iso(), run_id))
            conn.commit()
        finally:
            conn.close()

    # --- Events ---
    def add_event(
        self,
        *,
        event_type: str,
        actor: str,
        project_id: str,
        workstream_id: str,
        payload: dict[str, Any],
    ) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO events (ts, event_type, actor, project_id, workstream_id, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (utc_now_iso(), event_type, actor, project_id, workstream_id, json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def list_events(self, *, after_id: int = 0, limit: int = 200) -> list[EventRow]:
        lim = max(1, min(int(limit), 1000))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
                (int(after_id), lim),
            ).fetchall()
            out: list[EventRow] = []
            for r in rows:
                try:
                    payload = json.loads(r["payload_json"])
                except Exception:
                    payload = {"_raw": r["payload_json"]}
                out.append(
                    EventRow(
                        id=int(r["id"]),
                        ts=r["ts"],
                        event_type=r["event_type"],
                        actor=r["actor"],
                        project_id=r["project_id"],
                        workstream_id=r["workstream_id"],
                        payload=payload,
                    )
                )
            return out
        finally:
            conn.close()

    # --- Panel sync health/cache ---
    def record_panel_sync_run(
        self,
        *,
        project_id: str,
        panel_type: str,
        mode: str,
        dry_run: bool,
        ok: bool,
        stats: dict[str, Any],
        error: str = "",
        ts_start: Optional[str] = None,
        ts_end: Optional[str] = None,
    ) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO panel_sync_runs (ts_start, ts_end, project_id, panel_type, mode, dry_run, ok, stats_json, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_start or utc_now_iso(),
                    ts_end or utc_now_iso(),
                    str(project_id),
                    str(panel_type),
                    str(mode),
                    1 if dry_run else 0,
                    1 if ok else 0,
                    json.dumps(stats, ensure_ascii=False),
                    (error or "")[:2000],
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def get_last_panel_sync(self, *, project_id: Optional[str] = None, panel_type: str = "github_projects") -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            if project_id:
                row = conn.execute(
                    "SELECT * FROM panel_sync_runs WHERE panel_type = ? AND project_id = ? ORDER BY id DESC LIMIT 1",
                    (panel_type, str(project_id)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM panel_sync_runs WHERE panel_type = ? ORDER BY id DESC LIMIT 1",
                    (panel_type,),
                ).fetchone()
            if not row:
                return None
            try:
                stats = json.loads(row["stats_json"])
            except Exception:
                stats = {"_raw": row["stats_json"]}
            return {
                "id": int(row["id"]),
                "ts_start": row["ts_start"],
                "ts_end": row["ts_end"],
                "project_id": row["project_id"],
                "panel_type": row["panel_type"],
                "mode": row["mode"],
                "dry_run": bool(row["dry_run"]),
                "ok": bool(row["ok"]),
                "stats": stats,
                "error": row["error"],
            }
        finally:
            conn.close()

    def get_panel_sync_summary(self, *, project_id: str, panel_type: str = "github_projects") -> dict[str, Any]:
        """
        Lightweight health summary for a panel sync stream.
        """
        conn = self._connect()
        try:
            total = conn.execute(
                "SELECT COUNT(1) AS c FROM panel_sync_runs WHERE panel_type = ? AND project_id = ?",
                (str(panel_type), str(project_id)),
            ).fetchone()["c"]
            failures = conn.execute(
                "SELECT COUNT(1) AS c FROM panel_sync_runs WHERE panel_type = ? AND project_id = ? AND ok = 0",
                (str(panel_type), str(project_id)),
            ).fetchone()["c"]
            last_ok = conn.execute(
                "SELECT ts_end, mode, dry_run FROM panel_sync_runs WHERE panel_type = ? AND project_id = ? AND ok = 1 ORDER BY id DESC LIMIT 1",
                (str(panel_type), str(project_id)),
            ).fetchone()
            last_fail = conn.execute(
                "SELECT ts_end, mode, dry_run, error FROM panel_sync_runs WHERE panel_type = ? AND project_id = ? AND ok = 0 ORDER BY id DESC LIMIT 1",
                (str(panel_type), str(project_id)),
            ).fetchone()
            return {
                "runs_total": int(total or 0),
                "failures_total": int(failures or 0),
                "last_success": (
                    {"ts_end": last_ok["ts_end"], "mode": last_ok["mode"], "dry_run": bool(last_ok["dry_run"])} if last_ok else None
                ),
                "last_failure": (
                    {
                        "ts_end": last_fail["ts_end"],
                        "mode": last_fail["mode"],
                        "dry_run": bool(last_fail["dry_run"]),
                        "error": (last_fail["error"] or "")[:500],
                    }
                    if last_fail
                    else None
                ),
            }
        finally:
            conn.close()

    def set_panel_kv(self, *, key: str, value: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO panel_kv (key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_panel_kv(self, *, key: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM panel_kv WHERE key = ?", (key,)).fetchone()
            if not row:
                return None
            try:
                data = json.loads(row["value_json"])
            except Exception:
                data = {"_raw": row["value_json"]}
            return {"key": row["key"], "value": data, "updated_at": row["updated_at"]}
        finally:
            conn.close()
