import json
import os
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


@dataclass(frozen=True)
class NodeRow:
    instance_id: str
    role_preference: str
    base_url: str
    heartbeat_at: str
    capabilities: list[str]
    resources: dict[str, Any]
    agent_policy: dict[str, Any]
    tags: list[str]


@dataclass(frozen=True)
class TaskLeaseRow:
    lease_scope: str
    lease_key: str
    project_id: str
    task_id: str
    holder_instance_id: str
    holder_actor: str
    holder_meta: dict[str, Any]
    lease_ttl_sec: int
    lease_acquired_at: str
    lease_heartbeat_at: str
    lease_expires_at: str
    lease_version: int
    created_at: str
    updated_at: str


def _json_loads(raw: Any, *, default: Any) -> Any:
    try:
        return json.loads(str(raw or ""))
    except Exception:
        return default


def _lease_expires_at_iso(lease_ttl_sec: int) -> str:
    import datetime as _dt

    ttl = max(1, int(lease_ttl_sec or 1))
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=ttl)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _task_lease_is_active(lease_expires_at: str, *, now_iso: str) -> bool:
    return str(lease_expires_at or "").strip() > str(now_iso or "").strip()


def _task_lease_row(row: Any) -> TaskLeaseRow:
    return TaskLeaseRow(
        lease_scope=str(row["lease_scope"] or ""),
        lease_key=str(row["lease_key"] or ""),
        project_id=str(row["project_id"] or ""),
        task_id=str(row["task_id"] or ""),
        holder_instance_id=str(row["holder_instance_id"] or ""),
        holder_actor=str(row["holder_actor"] or ""),
        holder_meta=_json_loads(row["holder_meta_json"], default={}),
        lease_ttl_sec=int(row["lease_ttl_sec"] or 0),
        lease_acquired_at=str(row["lease_acquired_at"] or ""),
        lease_heartbeat_at=str(row["lease_heartbeat_at"] or ""),
        lease_expires_at=str(row["lease_expires_at"] or ""),
        lease_version=int(row["lease_version"] or 0),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


class SQLiteRuntimeDB:
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

            # Cluster node registry (local cache; GitHub is the authoritative bus when enabled).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                  instance_id TEXT PRIMARY KEY,
                  role_preference TEXT NOT NULL,
                  base_url TEXT NOT NULL,
                  heartbeat_at TEXT NOT NULL,
                  capabilities_json TEXT NOT NULL,
                  resources_json TEXT NOT NULL,
                  agent_policy_json TEXT NOT NULL,
                  tags_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_leases (
                  lease_scope TEXT NOT NULL,
                  lease_key TEXT PRIMARY KEY,
                  project_id TEXT NOT NULL,
                  task_id TEXT NOT NULL,
                  holder_instance_id TEXT NOT NULL,
                  holder_actor TEXT NOT NULL,
                  holder_meta_json TEXT NOT NULL,
                  lease_ttl_sec INTEGER NOT NULL,
                  lease_acquired_at TEXT NOT NULL,
                  lease_heartbeat_at TEXT NOT NULL,
                  lease_expires_at TEXT NOT NULL,
                  lease_version INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_leases_scope_expires ON task_leases(lease_scope, lease_expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_leases_holder ON task_leases(holder_instance_id, lease_expires_at)")

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                  namespace TEXT NOT NULL,
                  state_key TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (namespace, state_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_docs (
                  namespace TEXT NOT NULL,
                  doc_id TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  scope_id TEXT NOT NULL,
                  state TEXT NOT NULL,
                  category TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  PRIMARY KEY (namespace, doc_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_docs_ns_scope ON runtime_docs(namespace, scope_id, state, category, updated_at)")
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

    # --- Nodes (local registry cache) ---
    def upsert_node(
        self,
        *,
        instance_id: str,
        role_preference: str,
        base_url: str,
        capabilities: list[str],
        resources: dict[str, Any],
        agent_policy: dict[str, Any],
        tags: list[str],
    ) -> None:
        now = utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO nodes (instance_id, role_preference, base_url, heartbeat_at, capabilities_json, resources_json, agent_policy_json, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_id) DO UPDATE SET
                  role_preference=excluded.role_preference,
                  base_url=excluded.base_url,
                  heartbeat_at=excluded.heartbeat_at,
                  capabilities_json=excluded.capabilities_json,
                  resources_json=excluded.resources_json,
                  agent_policy_json=excluded.agent_policy_json,
                  tags_json=excluded.tags_json
                """,
                (
                    instance_id,
                    role_preference,
                    base_url,
                    now,
                    json.dumps(capabilities, ensure_ascii=False),
                    json.dumps(resources, ensure_ascii=False),
                    json.dumps(agent_policy, ensure_ascii=False),
                    json.dumps(tags, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def heartbeat_node(self, *, instance_id: str) -> None:
        now = utc_now_iso()
        conn = self._connect()
        try:
            conn.execute("UPDATE nodes SET heartbeat_at=? WHERE instance_id=?", (now, instance_id))
            conn.commit()
        finally:
            conn.close()

    def list_nodes(self) -> list[NodeRow]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM nodes ORDER BY heartbeat_at DESC").fetchall()
            out: list[NodeRow] = []
            for r in rows:
                out.append(
                    NodeRow(
                        instance_id=str(r["instance_id"]),
                        role_preference=str(r["role_preference"]),
                        base_url=str(r["base_url"]),
                        heartbeat_at=str(r["heartbeat_at"]),
                        capabilities=json.loads(str(r["capabilities_json"] or "[]") or "[]"),
                        resources=json.loads(str(r["resources_json"] or "{}") or "{}"),
                        agent_policy=json.loads(str(r["agent_policy_json"] or "{}") or "{}"),
                        tags=json.loads(str(r["tags_json"] or "[]") or "[]"),
                    )
                )
            return out
        finally:
            conn.close()

    # --- Task leases ---
    def claim_task_lease(
        self,
        *,
        lease_scope: str,
        lease_key: str,
        project_id: str,
        task_id: str,
        holder_instance_id: str,
        holder_actor: str,
        lease_ttl_sec: int,
        holder_meta: Optional[dict[str, Any]] = None,
    ) -> Optional[TaskLeaseRow]:
        now = utc_now_iso()
        exp = _lease_expires_at_iso(lease_ttl_sec)
        meta_json = json.dumps(holder_meta or {}, ensure_ascii=False)
        ttl = max(1, int(lease_ttl_sec or 1))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO task_leases (
                  lease_scope, lease_key, project_id, task_id, holder_instance_id, holder_actor, holder_meta_json,
                  lease_ttl_sec, lease_acquired_at, lease_heartbeat_at, lease_expires_at, lease_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(lease_scope),
                    str(lease_key),
                    str(project_id),
                    str(task_id),
                    str(holder_instance_id),
                    str(holder_actor),
                    meta_json,
                    ttl,
                    now,
                    now,
                    exp,
                    1,
                    now,
                    now,
                ),
            )
            if int(inserted.rowcount or 0) > 0:
                conn.commit()
                return self.get_task_lease(lease_key=lease_key)

            row = conn.execute("SELECT * FROM task_leases WHERE lease_key = ?", (str(lease_key),)).fetchone()
            if not row:
                conn.rollback()
                return None
            current = _task_lease_row(row)
            active = _task_lease_is_active(current.lease_expires_at, now_iso=now)
            if active and current.holder_instance_id and current.holder_instance_id != str(holder_instance_id):
                conn.rollback()
                return None
            acquired_at = current.lease_acquired_at if (active and current.holder_instance_id == str(holder_instance_id)) else now
            conn.execute(
                """
                UPDATE task_leases
                SET lease_scope = ?, project_id = ?, task_id = ?, holder_instance_id = ?, holder_actor = ?, holder_meta_json = ?,
                    lease_ttl_sec = ?, lease_acquired_at = ?, lease_heartbeat_at = ?, lease_expires_at = ?, lease_version = ?, updated_at = ?
                WHERE lease_key = ?
                """,
                (
                    str(lease_scope),
                    str(project_id),
                    str(task_id),
                    str(holder_instance_id),
                    str(holder_actor),
                    meta_json,
                    ttl,
                    acquired_at,
                    now,
                    exp,
                    int(current.lease_version) + 1,
                    now,
                    str(lease_key),
                ),
            )
            conn.commit()
            return self.get_task_lease(lease_key=lease_key)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def renew_task_lease(self, *, lease_key: str, holder_instance_id: str, lease_ttl_sec: int) -> Optional[TaskLeaseRow]:
        now = utc_now_iso()
        exp = _lease_expires_at_iso(lease_ttl_sec)
        ttl = max(1, int(lease_ttl_sec or 1))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM task_leases WHERE lease_key = ?", (str(lease_key),)).fetchone()
            if not row:
                conn.rollback()
                return None
            current = _task_lease_row(row)
            if current.holder_instance_id != str(holder_instance_id):
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE task_leases
                SET lease_ttl_sec = ?, lease_heartbeat_at = ?, lease_expires_at = ?, lease_version = ?, updated_at = ?
                WHERE lease_key = ?
                """,
                (ttl, now, exp, int(current.lease_version) + 1, now, str(lease_key)),
            )
            conn.commit()
            return self.get_task_lease(lease_key=lease_key)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_task_lease(self, *, lease_key: str) -> Optional[TaskLeaseRow]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM task_leases WHERE lease_key = ?", (str(lease_key),)).fetchone()
            return _task_lease_row(row) if row else None
        finally:
            conn.close()

    def release_task_lease(self, *, lease_key: str, holder_instance_id: str = "") -> bool:
        conn = self._connect()
        try:
            if str(holder_instance_id or "").strip():
                cur = conn.execute(
                    "DELETE FROM task_leases WHERE lease_key = ? AND holder_instance_id = ?",
                    (str(lease_key), str(holder_instance_id)),
                )
            else:
                cur = conn.execute("DELETE FROM task_leases WHERE lease_key = ?", (str(lease_key),))
            conn.commit()
            return int(cur.rowcount or 0) > 0
        finally:
            conn.close()

    def list_task_leases(
        self,
        *,
        lease_scope: str = "",
        holder_instance_id: str = "",
        active_only: bool = False,
        limit: int = 1000,
    ) -> list[TaskLeaseRow]:
        clauses: list[str] = []
        params: list[Any] = []
        if str(lease_scope or "").strip():
            clauses.append("lease_scope = ?")
            params.append(str(lease_scope))
        if str(holder_instance_id or "").strip():
            clauses.append("holder_instance_id = ?")
            params.append(str(holder_instance_id))
        if active_only:
            clauses.append("lease_expires_at > ?")
            params.append(utc_now_iso())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit or 1000), 5000)))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM task_leases {where} ORDER BY lease_expires_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [_task_lease_row(row) for row in rows]
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

    # --- Generic runtime state/docs ---
    def put_runtime_state(self, *, namespace: str, state_key: str, value: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO runtime_state (namespace, state_key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, state_key) DO UPDATE SET
                  value_json=excluded.value_json,
                  updated_at=excluded.updated_at
                """,
                (str(namespace), str(state_key), json.dumps(value, ensure_ascii=False), utc_now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_runtime_state(self, *, namespace: str, state_key: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value_json, updated_at FROM runtime_state WHERE namespace = ? AND state_key = ?",
                (str(namespace), str(state_key)),
            ).fetchone()
            if not row:
                return None
            try:
                value = json.loads(str(row["value_json"] or "{}"))
            except Exception:
                value = {"_raw": row["value_json"]}
            return {"namespace": str(namespace), "state_key": str(state_key), "value": value, "updated_at": str(row["updated_at"])}
        finally:
            conn.close()

    def delete_runtime_state(self, *, namespace: str, state_key: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM runtime_state WHERE namespace = ? AND state_key = ?", (str(namespace), str(state_key)))
            conn.commit()
        finally:
            conn.close()

    def list_runtime_state(self, *, namespace: str, prefix: str = "") -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if prefix:
                rows = conn.execute(
                    "SELECT state_key, value_json, updated_at FROM runtime_state WHERE namespace = ? AND state_key LIKE ? ORDER BY state_key ASC",
                    (str(namespace), f"{str(prefix)}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT state_key, value_json, updated_at FROM runtime_state WHERE namespace = ? ORDER BY state_key ASC",
                    (str(namespace),),
                ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                try:
                    value = json.loads(str(row["value_json"] or "{}"))
                except Exception:
                    value = {"_raw": row["value_json"]}
                out.append({"namespace": str(namespace), "state_key": str(row["state_key"]), "value": value, "updated_at": str(row["updated_at"])})
            return out
        finally:
            conn.close()

    def put_runtime_doc(
        self,
        *,
        namespace: str,
        doc_id: str,
        project_id: str,
        scope_id: str,
        state: str,
        category: str,
        value: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO runtime_docs (namespace, doc_id, project_id, scope_id, state, category, created_at, updated_at, value_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, doc_id) DO UPDATE SET
                  project_id=excluded.project_id,
                  scope_id=excluded.scope_id,
                  state=excluded.state,
                  category=excluded.category,
                  updated_at=excluded.updated_at,
                  value_json=excluded.value_json
                """,
                (
                    str(namespace),
                    str(doc_id),
                    str(project_id),
                    str(scope_id),
                    str(state),
                    str(category),
                    now,
                    now,
                    json.dumps(value, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_runtime_doc(self, *, namespace: str, doc_id: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT project_id, scope_id, state, category, created_at, updated_at, value_json
                FROM runtime_docs WHERE namespace = ? AND doc_id = ?
                """,
                (str(namespace), str(doc_id)),
            ).fetchone()
            if not row:
                return None
            try:
                value = json.loads(str(row["value_json"] or "{}"))
            except Exception:
                value = {"_raw": row["value_json"]}
            return {
                "namespace": str(namespace),
                "doc_id": str(doc_id),
                "project_id": str(row["project_id"]),
                "scope_id": str(row["scope_id"]),
                "state": str(row["state"]),
                "category": str(row["category"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "value": value,
            }
        finally:
            conn.close()

    def delete_runtime_doc(self, *, namespace: str, doc_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM runtime_docs WHERE namespace = ? AND doc_id = ?", (str(namespace), str(doc_id)))
            conn.commit()
        finally:
            conn.close()

    def list_runtime_docs(
        self,
        *,
        namespace: str,
        project_id: Optional[str] = None,
        scope_id: Optional[str] = None,
        state: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses = ["namespace = ?"]
        params: list[Any] = [str(namespace)]
        if project_id is not None and str(project_id).strip():
            clauses.append("project_id = ?")
            params.append(str(project_id))
        if scope_id is not None and str(scope_id).strip():
            clauses.append("scope_id = ?")
            params.append(str(scope_id))
        if state is not None and str(state).strip():
            clauses.append("state = ?")
            params.append(str(state))
        if category is not None and str(category).strip():
            clauses.append("category = ?")
            params.append(str(category))
        params.append(max(1, min(int(limit), 5000)))
        query = (
            "SELECT doc_id, project_id, scope_id, state, category, created_at, updated_at, value_json "
            f"FROM runtime_docs WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
        )
        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                try:
                    value = json.loads(str(row["value_json"] or "{}"))
                except Exception:
                    value = {"_raw": row["value_json"]}
                out.append(
                    {
                        "namespace": str(namespace),
                        "doc_id": str(row["doc_id"]),
                        "project_id": str(row["project_id"]),
                        "scope_id": str(row["scope_id"]),
                        "state": str(row["state"]),
                        "category": str(row["category"]),
                        "created_at": str(row["created_at"]),
                        "updated_at": str(row["updated_at"]),
                        "value": value,
                    }
                )
            return out
        finally:
            conn.close()


def _is_postgres_dsn(s: str) -> bool:
    s = (s or "").strip().lower()
    return s.startswith("postgres://") or s.startswith("postgresql://")


class PostgresRuntimeDB:
    """
    Postgres backend for RuntimeDB.

    Enabled by env:
      TEAMOS_DB_URL=postgresql://...
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._init()

    def _connect(self):
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore

        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _init(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
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
            cur.execute(
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id BIGSERIAL PRIMARY KEY,
                  ts TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  workstream_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                  instance_id TEXT PRIMARY KEY,
                  role_preference TEXT NOT NULL,
                  base_url TEXT NOT NULL,
                  heartbeat_at TEXT NOT NULL,
                  capabilities_json TEXT NOT NULL,
                  resources_json TEXT NOT NULL,
                  agent_policy_json TEXT NOT NULL,
                  tags_json TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS task_leases (
                  lease_scope TEXT NOT NULL,
                  lease_key TEXT PRIMARY KEY,
                  project_id TEXT NOT NULL,
                  task_id TEXT NOT NULL,
                  holder_instance_id TEXT NOT NULL,
                  holder_actor TEXT NOT NULL,
                  holder_meta_json TEXT NOT NULL,
                  lease_ttl_sec INTEGER NOT NULL,
                  lease_acquired_at TEXT NOT NULL,
                  lease_heartbeat_at TEXT NOT NULL,
                  lease_expires_at TEXT NOT NULL,
                  lease_version BIGINT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_task_leases_scope_expires ON task_leases(lease_scope, lease_expires_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_task_leases_holder ON task_leases(holder_instance_id, lease_expires_at)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS panel_sync_runs (
                  id BIGSERIAL PRIMARY KEY,
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS panel_kv (
                  key TEXT PRIMARY KEY,
                  value_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                  namespace TEXT NOT NULL,
                  state_key TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (namespace, state_key)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_docs (
                  namespace TEXT NOT NULL,
                  doc_id TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  scope_id TEXT NOT NULL,
                  state TEXT NOT NULL,
                  category TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  PRIMARY KEY (namespace, doc_id)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_runtime_docs_ns_scope ON runtime_docs(namespace, scope_id, state, category, updated_at)")
            conn.commit()
        finally:
            conn.close()

    # --- Agents ---
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
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (agent_id, role_id, project_id, workstream_id, task_id, state, now, now, current_action),
            )
            conn.commit()
            return agent_id
        finally:
            conn.close()

    def heartbeat(self, *, agent_id: str, state: Optional[str] = None, current_action: Optional[str] = None) -> None:
        now = utc_now_iso()
        sets = ["last_heartbeat = %s"]
        params: list[Any] = [now]
        if state is not None:
            sets.append("state = %s")
            params.append(state)
        if current_action is not None:
            sets.append("current_action = %s")
            params.append(current_action)
        params.append(agent_id)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE agent_id = %s", params)
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
            sets.append("project_id = %s")
            params.append(project_id)
        if workstream_id is not None:
            sets.append("workstream_id = %s")
            params.append(workstream_id)
        if task_id is not None:
            sets.append("task_id = %s")
            params.append(task_id)
        if state is not None:
            sets.append("state = %s")
            params.append(state)
        if current_action is not None:
            sets.append("current_action = %s")
            params.append(current_action)
        sets.append("last_heartbeat = %s")
        params.append(utc_now_iso())
        params.append(agent_id)
        if not sets:
            return
        conn = self._connect()
        try:
            conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE agent_id = %s", params)
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
            clauses.append("project_id = %s")
            params.append(project_id)
        if workstream_id:
            clauses.append("workstream_id = %s")
            params.append(workstream_id)
        if state:
            clauses.append("state = %s")
            params.append(state)
        if role_id:
            clauses.append("role_id = %s")
            params.append(role_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT * FROM agents {where} ORDER BY started_at ASC", params).fetchall()
            return [
                AgentRow(
                    agent_id=str(r["agent_id"]),
                    role_id=str(r["role_id"]),
                    project_id=str(r["project_id"]),
                    workstream_id=str(r["workstream_id"]),
                    task_id=str(r["task_id"]),
                    state=str(r["state"]),
                    started_at=str(r["started_at"]),
                    last_heartbeat=str(r["last_heartbeat"]),
                    current_action=str(r["current_action"]),
                )
                for r in rows
            ]
        finally:
            conn.close()

    # --- Task leases ---
    def claim_task_lease(
        self,
        *,
        lease_scope: str,
        lease_key: str,
        project_id: str,
        task_id: str,
        holder_instance_id: str,
        holder_actor: str,
        lease_ttl_sec: int,
        holder_meta: Optional[dict[str, Any]] = None,
    ) -> Optional[TaskLeaseRow]:
        now = utc_now_iso()
        exp = _lease_expires_at_iso(lease_ttl_sec)
        meta_json = json.dumps(holder_meta or {}, ensure_ascii=False)
        ttl = max(1, int(lease_ttl_sec or 1))
        conn = self._connect()
        try:
            inserted = conn.execute(
                """
                INSERT INTO task_leases (
                  lease_scope, lease_key, project_id, task_id, holder_instance_id, holder_actor, holder_meta_json,
                  lease_ttl_sec, lease_acquired_at, lease_heartbeat_at, lease_expires_at, lease_version, created_at, updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (lease_key) DO NOTHING
                """,
                (
                    str(lease_scope),
                    str(lease_key),
                    str(project_id),
                    str(task_id),
                    str(holder_instance_id),
                    str(holder_actor),
                    meta_json,
                    ttl,
                    now,
                    now,
                    exp,
                    1,
                    now,
                    now,
                ),
            )
            if int(inserted.rowcount or 0) > 0:
                conn.commit()
                return self.get_task_lease(lease_key=lease_key)

            row = conn.execute("SELECT * FROM task_leases WHERE lease_key = %s FOR UPDATE", (str(lease_key),)).fetchone()
            if not row:
                conn.rollback()
                return None
            current = _task_lease_row(row)
            active = _task_lease_is_active(current.lease_expires_at, now_iso=now)
            if active and current.holder_instance_id and current.holder_instance_id != str(holder_instance_id):
                conn.rollback()
                return None
            acquired_at = current.lease_acquired_at if (active and current.holder_instance_id == str(holder_instance_id)) else now
            conn.execute(
                """
                UPDATE task_leases
                SET lease_scope = %s, project_id = %s, task_id = %s, holder_instance_id = %s, holder_actor = %s, holder_meta_json = %s,
                    lease_ttl_sec = %s, lease_acquired_at = %s, lease_heartbeat_at = %s, lease_expires_at = %s, lease_version = %s, updated_at = %s
                WHERE lease_key = %s
                """,
                (
                    str(lease_scope),
                    str(project_id),
                    str(task_id),
                    str(holder_instance_id),
                    str(holder_actor),
                    meta_json,
                    ttl,
                    acquired_at,
                    now,
                    exp,
                    int(current.lease_version) + 1,
                    now,
                    str(lease_key),
                ),
            )
            conn.commit()
            return self.get_task_lease(lease_key=lease_key)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def renew_task_lease(self, *, lease_key: str, holder_instance_id: str, lease_ttl_sec: int) -> Optional[TaskLeaseRow]:
        now = utc_now_iso()
        exp = _lease_expires_at_iso(lease_ttl_sec)
        ttl = max(1, int(lease_ttl_sec or 1))
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM task_leases WHERE lease_key = %s FOR UPDATE", (str(lease_key),)).fetchone()
            if not row:
                conn.rollback()
                return None
            current = _task_lease_row(row)
            if current.holder_instance_id != str(holder_instance_id):
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE task_leases
                SET lease_ttl_sec = %s, lease_heartbeat_at = %s, lease_expires_at = %s, lease_version = %s, updated_at = %s
                WHERE lease_key = %s
                """,
                (ttl, now, exp, int(current.lease_version) + 1, now, str(lease_key)),
            )
            conn.commit()
            return self.get_task_lease(lease_key=lease_key)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_task_lease(self, *, lease_key: str) -> Optional[TaskLeaseRow]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM task_leases WHERE lease_key = %s", (str(lease_key),)).fetchone()
            return _task_lease_row(row) if row else None
        finally:
            conn.close()

    def release_task_lease(self, *, lease_key: str, holder_instance_id: str = "") -> bool:
        conn = self._connect()
        try:
            if str(holder_instance_id or "").strip():
                cur = conn.execute(
                    "DELETE FROM task_leases WHERE lease_key = %s AND holder_instance_id = %s",
                    (str(lease_key), str(holder_instance_id)),
                )
            else:
                cur = conn.execute("DELETE FROM task_leases WHERE lease_key = %s", (str(lease_key),))
            conn.commit()
            return int(cur.rowcount or 0) > 0
        finally:
            conn.close()

    def list_task_leases(
        self,
        *,
        lease_scope: str = "",
        holder_instance_id: str = "",
        active_only: bool = False,
        limit: int = 1000,
    ) -> list[TaskLeaseRow]:
        clauses: list[str] = []
        params: list[Any] = []
        if str(lease_scope or "").strip():
            clauses.append("lease_scope = %s")
            params.append(str(lease_scope))
        if str(holder_instance_id or "").strip():
            clauses.append("holder_instance_id = %s")
            params.append(str(holder_instance_id))
        if active_only:
            clauses.append("lease_expires_at > %s")
            params.append(utc_now_iso())
        params.append(max(1, min(int(limit or 1000), 5000)))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM task_leases {where} ORDER BY lease_expires_at DESC LIMIT %s",
                params,
            ).fetchall()
            return [_task_lease_row(row) for row in rows]
        finally:
            conn.close()

    # --- Runs ---
    def upsert_run(self, *, run_id: Optional[str], project_id: str, workstream_id: str, objective: str, state: str) -> str:
        rid = run_id or str(uuid.uuid4())
        now = utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO runs (run_id, project_id, workstream_id, objective, state, started_at, last_update)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(run_id) DO UPDATE SET
                  project_id=EXCLUDED.project_id,
                  workstream_id=EXCLUDED.workstream_id,
                  objective=EXCLUDED.objective,
                  state=EXCLUDED.state,
                  last_update=EXCLUDED.last_update
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
            clauses.append("project_id = %s")
            params.append(project_id)
        if workstream_id:
            clauses.append("workstream_id = %s")
            params.append(workstream_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT * FROM runs {where} ORDER BY started_at DESC", params).fetchall()
            return [
                RunRow(
                    run_id=str(r["run_id"]),
                    project_id=str(r["project_id"]),
                    workstream_id=str(r["workstream_id"]),
                    objective=str(r["objective"]),
                    state=str(r["state"]),
                    started_at=str(r["started_at"]),
                    last_update=str(r["last_update"]),
                )
                for r in rows
            ]
        finally:
            conn.close()

    def get_run(self, run_id: str) -> Optional[RunRow]:
        conn = self._connect()
        try:
            r = conn.execute("SELECT * FROM runs WHERE run_id = %s", (run_id,)).fetchone()
            if not r:
                return None
            return RunRow(
                run_id=str(r["run_id"]),
                project_id=str(r["project_id"]),
                workstream_id=str(r["workstream_id"]),
                objective=str(r["objective"]),
                state=str(r["state"]),
                started_at=str(r["started_at"]),
                last_update=str(r["last_update"]),
            )
        finally:
            conn.close()

    def update_run_state(self, *, run_id: str, state: str) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE runs SET state=%s, last_update=%s WHERE run_id=%s", (state, utc_now_iso(), run_id))
            conn.commit()
        finally:
            conn.close()

    # --- Events ---
    def add_event(self, *, event_type: str, actor: str, project_id: str, workstream_id: str, payload: dict[str, Any]) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "INSERT INTO events (ts, event_type, actor, project_id, workstream_id, payload_json) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (utc_now_iso(), event_type, actor, project_id, workstream_id, json.dumps(payload, ensure_ascii=False)),
            ).fetchone()
            conn.commit()
            return int(row["id"]) if row and row.get("id") is not None else 0
        finally:
            conn.close()

    def list_events(self, *, after_id: int = 0, limit: int = 200) -> list[EventRow]:
        lim = max(1, min(int(limit), 1000))
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM events WHERE id > %s ORDER BY id ASC LIMIT %s", (int(after_id), lim)).fetchall()
            out: list[EventRow] = []
            for r in rows:
                try:
                    payload = json.loads(str(r["payload_json"] or "{}"))
                except Exception:
                    payload = {"_raw": r.get("payload_json")}
                out.append(
                    EventRow(
                        id=int(r["id"]),
                        ts=str(r["ts"]),
                        event_type=str(r["event_type"]),
                        actor=str(r["actor"]),
                        project_id=str(r["project_id"]),
                        workstream_id=str(r["workstream_id"]),
                        payload=payload,
                    )
                )
            return out
        finally:
            conn.close()

    # --- Nodes ---
    def upsert_node(
        self,
        *,
        instance_id: str,
        role_preference: str,
        base_url: str,
        capabilities: list[str],
        resources: dict[str, Any],
        agent_policy: dict[str, Any],
        tags: list[str],
    ) -> None:
        now = utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO nodes (instance_id, role_preference, base_url, heartbeat_at, capabilities_json, resources_json, agent_policy_json, tags_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(instance_id) DO UPDATE SET
                  role_preference=EXCLUDED.role_preference,
                  base_url=EXCLUDED.base_url,
                  heartbeat_at=EXCLUDED.heartbeat_at,
                  capabilities_json=EXCLUDED.capabilities_json,
                  resources_json=EXCLUDED.resources_json,
                  agent_policy_json=EXCLUDED.agent_policy_json,
                  tags_json=EXCLUDED.tags_json
                """,
                (
                    instance_id,
                    role_preference,
                    base_url,
                    now,
                    json.dumps(capabilities, ensure_ascii=False),
                    json.dumps(resources, ensure_ascii=False),
                    json.dumps(agent_policy, ensure_ascii=False),
                    json.dumps(tags, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def heartbeat_node(self, *, instance_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE nodes SET heartbeat_at=%s WHERE instance_id=%s", (utc_now_iso(), instance_id))
            conn.commit()
        finally:
            conn.close()

    def list_nodes(self) -> list[NodeRow]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM nodes ORDER BY heartbeat_at DESC").fetchall()
            out: list[NodeRow] = []
            for r in rows:
                out.append(
                    NodeRow(
                        instance_id=str(r["instance_id"]),
                        role_preference=str(r["role_preference"]),
                        base_url=str(r["base_url"]),
                        heartbeat_at=str(r["heartbeat_at"]),
                        capabilities=json.loads(str(r["capabilities_json"] or "[]") or "[]"),
                        resources=json.loads(str(r["resources_json"] or "{}") or "{}"),
                        agent_policy=json.loads(str(r["agent_policy_json"] or "{}") or "{}"),
                        tags=json.loads(str(r["tags_json"] or "[]") or "[]"),
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
            row = conn.execute(
                """
                INSERT INTO panel_sync_runs (ts_start, ts_end, project_id, panel_type, mode, dry_run, ok, stats_json, error)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
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
            ).fetchone()
            conn.commit()
            return int(row["id"]) if row and row.get("id") is not None else 0
        finally:
            conn.close()

    def get_last_panel_sync(self, *, project_id: Optional[str] = None, panel_type: str = "github_projects") -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            if project_id:
                row = conn.execute(
                    "SELECT * FROM panel_sync_runs WHERE panel_type=%s AND project_id=%s ORDER BY id DESC LIMIT 1",
                    (panel_type, str(project_id)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM panel_sync_runs WHERE panel_type=%s ORDER BY id DESC LIMIT 1",
                    (panel_type,),
                ).fetchone()
            if not row:
                return None
            try:
                stats = json.loads(str(row["stats_json"] or "{}"))
            except Exception:
                stats = {"_raw": row.get("stats_json")}
            return {
                "id": int(row["id"]),
                "ts_start": str(row["ts_start"]),
                "ts_end": str(row["ts_end"]),
                "project_id": str(row["project_id"]),
                "panel_type": str(row["panel_type"]),
                "mode": str(row["mode"]),
                "dry_run": bool(int(row["dry_run"])),
                "ok": bool(int(row["ok"])),
                "stats": stats,
                "error": str(row.get("error") or ""),
            }
        finally:
            conn.close()

    def get_panel_sync_summary(self, *, project_id: str, panel_type: str = "github_projects") -> dict[str, Any]:
        conn = self._connect()
        try:
            total = conn.execute(
                "SELECT COUNT(1) AS c FROM panel_sync_runs WHERE panel_type=%s AND project_id=%s",
                (str(panel_type), str(project_id)),
            ).fetchone()["c"]
            failures = conn.execute(
                "SELECT COUNT(1) AS c FROM panel_sync_runs WHERE panel_type=%s AND project_id=%s AND ok=0",
                (str(panel_type), str(project_id)),
            ).fetchone()["c"]
            last_ok = conn.execute(
                "SELECT ts_end, mode, dry_run FROM panel_sync_runs WHERE panel_type=%s AND project_id=%s AND ok=1 ORDER BY id DESC LIMIT 1",
                (str(panel_type), str(project_id)),
            ).fetchone()
            last_fail = conn.execute(
                "SELECT ts_end, mode, dry_run, error FROM panel_sync_runs WHERE panel_type=%s AND project_id=%s AND ok=0 ORDER BY id DESC LIMIT 1",
                (str(panel_type), str(project_id)),
            ).fetchone()
            return {
                "runs_total": int(total or 0),
                "failures_total": int(failures or 0),
                "last_success": (
                    {"ts_end": last_ok["ts_end"], "mode": last_ok["mode"], "dry_run": bool(int(last_ok["dry_run"]))} if last_ok else None
                ),
                "last_failure": (
                    {
                        "ts_end": last_fail["ts_end"],
                        "mode": last_fail["mode"],
                        "dry_run": bool(int(last_fail["dry_run"])),
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
                INSERT INTO panel_kv (key, value_json, updated_at) VALUES (%s,%s,%s)
                ON CONFLICT(key) DO UPDATE SET value_json=EXCLUDED.value_json, updated_at=EXCLUDED.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_panel_kv(self, *, key: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM panel_kv WHERE key=%s", (key,)).fetchone()
            if not row:
                return None
            try:
                data = json.loads(str(row["value_json"] or "{}"))
            except Exception:
                data = {"_raw": row.get("value_json")}
            return {"key": str(row["key"]), "value": data, "updated_at": str(row["updated_at"])}
        finally:
            conn.close()

    # --- Generic runtime state/docs ---
    def put_runtime_state(self, *, namespace: str, state_key: str, value: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO runtime_state (namespace, state_key, value_json, updated_at)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT(namespace, state_key) DO UPDATE SET
                  value_json=EXCLUDED.value_json,
                  updated_at=EXCLUDED.updated_at
                """,
                (str(namespace), str(state_key), json.dumps(value, ensure_ascii=False), utc_now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_runtime_state(self, *, namespace: str, state_key: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value_json, updated_at FROM runtime_state WHERE namespace=%s AND state_key=%s",
                (str(namespace), str(state_key)),
            ).fetchone()
            if not row:
                return None
            try:
                value = json.loads(str(row["value_json"] or "{}"))
            except Exception:
                value = {"_raw": row.get("value_json")}
            return {"namespace": str(namespace), "state_key": str(state_key), "value": value, "updated_at": str(row["updated_at"])}
        finally:
            conn.close()

    def delete_runtime_state(self, *, namespace: str, state_key: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM runtime_state WHERE namespace=%s AND state_key=%s", (str(namespace), str(state_key)))
            conn.commit()
        finally:
            conn.close()

    def list_runtime_state(self, *, namespace: str, prefix: str = "") -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if prefix:
                rows = conn.execute(
                    "SELECT state_key, value_json, updated_at FROM runtime_state WHERE namespace=%s AND state_key LIKE %s ORDER BY state_key ASC",
                    (str(namespace), f"{str(prefix)}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT state_key, value_json, updated_at FROM runtime_state WHERE namespace=%s ORDER BY state_key ASC",
                    (str(namespace),),
                ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                try:
                    value = json.loads(str(row["value_json"] or "{}"))
                except Exception:
                    value = {"_raw": row.get("value_json")}
                out.append({"namespace": str(namespace), "state_key": str(row["state_key"]), "value": value, "updated_at": str(row["updated_at"])})
            return out
        finally:
            conn.close()

    def put_runtime_doc(
        self,
        *,
        namespace: str,
        doc_id: str,
        project_id: str,
        scope_id: str,
        state: str,
        category: str,
        value: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO runtime_docs (namespace, doc_id, project_id, scope_id, state, category, created_at, updated_at, value_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(namespace, doc_id) DO UPDATE SET
                  project_id=EXCLUDED.project_id,
                  scope_id=EXCLUDED.scope_id,
                  state=EXCLUDED.state,
                  category=EXCLUDED.category,
                  updated_at=EXCLUDED.updated_at,
                  value_json=EXCLUDED.value_json
                """,
                (
                    str(namespace),
                    str(doc_id),
                    str(project_id),
                    str(scope_id),
                    str(state),
                    str(category),
                    now,
                    now,
                    json.dumps(value, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_runtime_doc(self, *, namespace: str, doc_id: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT project_id, scope_id, state, category, created_at, updated_at, value_json
                FROM runtime_docs WHERE namespace=%s AND doc_id=%s
                """,
                (str(namespace), str(doc_id)),
            ).fetchone()
            if not row:
                return None
            try:
                value = json.loads(str(row["value_json"] or "{}"))
            except Exception:
                value = {"_raw": row.get("value_json")}
            return {
                "namespace": str(namespace),
                "doc_id": str(doc_id),
                "project_id": str(row["project_id"]),
                "scope_id": str(row["scope_id"]),
                "state": str(row["state"]),
                "category": str(row["category"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "value": value,
            }
        finally:
            conn.close()

    def delete_runtime_doc(self, *, namespace: str, doc_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM runtime_docs WHERE namespace=%s AND doc_id=%s", (str(namespace), str(doc_id)))
            conn.commit()
        finally:
            conn.close()

    def list_runtime_docs(
        self,
        *,
        namespace: str,
        project_id: Optional[str] = None,
        scope_id: Optional[str] = None,
        state: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses = ["namespace = %s"]
        params: list[Any] = [str(namespace)]
        if project_id is not None and str(project_id).strip():
            clauses.append("project_id = %s")
            params.append(str(project_id))
        if scope_id is not None and str(scope_id).strip():
            clauses.append("scope_id = %s")
            params.append(str(scope_id))
        if state is not None and str(state).strip():
            clauses.append("state = %s")
            params.append(str(state))
        if category is not None and str(category).strip():
            clauses.append("category = %s")
            params.append(str(category))
        params.append(max(1, min(int(limit), 5000)))
        query = (
            "SELECT doc_id, project_id, scope_id, state, category, created_at, updated_at, value_json "
            f"FROM runtime_docs WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT %s"
        )
        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                try:
                    value = json.loads(str(row["value_json"] or "{}"))
                except Exception:
                    value = {"_raw": row.get("value_json")}
                out.append(
                    {
                        "namespace": str(namespace),
                        "doc_id": str(row["doc_id"]),
                        "project_id": str(row["project_id"]),
                        "scope_id": str(row["scope_id"]),
                        "state": str(row["state"]),
                        "category": str(row["category"]),
                        "created_at": str(row["created_at"]),
                        "updated_at": str(row["updated_at"]),
                        "value": value,
                    }
                )
            return out
        finally:
            conn.close()


class RuntimeDB:
    """
    RuntimeDB facade.

    - Default: sqlite (db_path from TEAMOS_RUNTIME_DB_PATH or .team-os/state/runtime.db)
    - Optional: Postgres when TEAMOS_DB_URL is set to a postgres DSN.
    """

    def __init__(self, db_path_or_url: str):
        dsn = (os.getenv("TEAMOS_DB_URL") or "").strip() or str(db_path_or_url)
        if _is_postgres_dsn(dsn):
            self._impl = PostgresRuntimeDB(dsn)
        else:
            self._impl = SQLiteRuntimeDB(db_path_or_url)

    def __getattr__(self, name: str):
        return getattr(self._impl, name)
