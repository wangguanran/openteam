#!/usr/bin/env python3
"""
Deterministic locking (repo lock + scope lock).

Design:
- Preferred: Postgres advisory lock when TEAMOS_DB_URL is available.
- Fallback: file lock with TTL + heartbeat renew (crash recovery).

Notes:
- This module is intentionally dependency-light (stdlib; optional psycopg).
- Lock files store holder metadata for diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from _common import runtime_hub_root, runtime_state_root, runtime_workspace_root


class LockBusy(RuntimeError):
    def __init__(self, *, lock_key: str, backend: str, holder: Optional[dict[str, Any]], waited_sec: float):
        self.lock_key = lock_key
        self.backend = backend
        self.holder = holder or {}
        self.waited_sec = float(waited_sec)
        super().__init__(f"LOCK_BUSY lock_key={lock_key} backend={backend} waited_sec={waited_sec:.2f} holder={self.holder}")


class DbUnavailable(RuntimeError):
    """
    Raised when TEAMOS_DB_URL is configured but the DB backend cannot be used
    (missing driver / connection failure). This allows falling back to file
    locks without conflating "lock contention" with "DB unavailable".
    """


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_from_epoch(ts: float) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _epoch_from_iso(s: str) -> float:
    s = str(s or "").strip()
    if not s:
        return 0.0
    try:
        import datetime as _dt

        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        return _dt.datetime.fromisoformat(s2).timestamp()
    except Exception:
        return 0.0


def _advisory_key64(lock_key: str) -> int:
    """
    Postgres advisory locks accept BIGINT (signed int64).
    Derive a stable signed 64-bit int from lock_key.
    """
    h = hashlib.sha256(str(lock_key).encode("utf-8")).digest()[:8]
    u = int.from_bytes(h, "big", signed=False)
    if u >= 2**63:
        return int(u - 2**64)
    return int(u)


def _default_holder(*, instance_id: str = "", agent_id: str = "", task_id: str = "") -> dict[str, Any]:
    return {
        "instance_id": str(instance_id or "").strip(),
        "agent_id": str(agent_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
    }


def _db_dsn() -> str:
    return str(os.getenv("TEAMOS_DB_URL") or "").strip()


def _runtime_override_for_repo(repo_root: Path) -> str:
    """
    Keep runtime-root contract deterministic per repo unless env override is set.
    """
    if str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip():
        return ""
    return str(repo_root.parent / "team-os-runtime")


def _can_use_db(dsn: str) -> bool:
    s = str(dsn or "").strip().lower()
    return bool(s) and (s.startswith("postgres://") or s.startswith("postgresql://"))


def _connect_db(dsn: str):
    import psycopg  # type: ignore

    # Keep short connect timeout for lock acquisition.
    try:
        return psycopg.connect(dsn, connect_timeout=5)
    except TypeError:  # pragma: no cover
        return psycopg.connect(dsn)


def _read_lock_file(path: Path) -> dict[str, Any]:
    try:
        s = path.read_text(encoding="utf-8", errors="replace").strip()
        if not s:
            return {}
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _write_lock_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def _is_expired(meta: dict[str, Any], *, now_epoch: float) -> bool:
    exp = _epoch_from_iso(str(meta.get("expires_at") or ""))
    return exp > 0 and exp <= now_epoch


@dataclass
class LockHandle:
    lock_key: str
    backend: str  # db_advisory | file
    holder: dict[str, Any]
    acquired_at: str
    expires_at: str
    _lock_path: Optional[Path] = None
    _conn: Any = None
    _stop: Optional[threading.Event] = None
    _renew_thread: Optional[threading.Thread] = None
    _ttl_sec: int = 0

    def renew(self) -> None:
        if self.backend != "file":
            return
        if not self._lock_path:
            return
        now = time.time()
        meta = _read_lock_file(self._lock_path)
        # Only renew if we still own it.
        if str(meta.get("holder", {}).get("pid")) != str(self.holder.get("pid")):
            return
        if str(meta.get("holder", {}).get("hostname")) != str(self.holder.get("hostname")):
            return
        meta["heartbeat_at"] = _utc_now_iso()
        meta["expires_at"] = _iso_from_epoch(now + float(self._ttl_sec or 0))
        _write_lock_file(self._lock_path, meta)
        self.expires_at = str(meta.get("expires_at") or self.expires_at)

    def release(self) -> None:
        # Stop renew thread first.
        try:
            if self._stop is not None:
                self._stop.set()
            if self._renew_thread is not None:
                self._renew_thread.join(timeout=1.0)
        except Exception:
            pass

        if self.backend == "db_advisory":
            try:
                if self._conn is not None:
                    k = _advisory_key64(self.lock_key)
                    with self._conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_unlock(%s)", (k,))
                    self._conn.close()
            except Exception:
                try:
                    if self._conn is not None:
                        self._conn.close()
                except Exception:
                    pass
            return

        if self.backend == "file" and self._lock_path is not None:
            try:
                meta = _read_lock_file(self._lock_path)
                if str(meta.get("holder", {}).get("pid")) == str(self.holder.get("pid")) and str(meta.get("holder", {}).get("hostname")) == str(self.holder.get("hostname")):
                    try:
                        self._lock_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except TypeError:  # pragma: no cover (py<3.8)
                        if self._lock_path.exists():
                            self._lock_path.unlink()
            except Exception:
                pass

    def __enter__(self) -> "LockHandle":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _start_file_renew_thread(h: LockHandle) -> None:
    if h.backend != "file" or not h._lock_path:
        return
    stop = threading.Event()
    h._stop = stop
    interval = max(1.0, float(h._ttl_sec or 0) / 3.0)

    def _loop() -> None:
        while not stop.is_set():
            time.sleep(interval)
            if stop.is_set():
                break
            try:
                h.renew()
            except Exception:
                # Best-effort renew; if it fails, TTL will eventually expire.
                pass

    t = threading.Thread(target=_loop, name=f"lock-renew:{h.lock_key}", daemon=True)
    h._renew_thread = t
    t.start()


def _acquire_file_lock(
    *,
    lock_key: str,
    lock_path: Path,
    holder: dict[str, Any],
    ttl_sec: int,
    wait_sec: float,
    poll_sec: float,
) -> LockHandle:
    start = time.time()
    deadline = start + float(wait_sec or 0)

    while True:
        now = time.time()
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                meta = {
                    "schema_version": 1,
                    "lock_key": lock_key,
                    "backend": "file",
                    "holder": holder,
                    "acquired_at": _utc_now_iso(),
                    "heartbeat_at": _utc_now_iso(),
                    "expires_at": _iso_from_epoch(now + float(ttl_sec or 0)),
                }
                os.write(fd, (json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)

            h = LockHandle(
                lock_key=lock_key,
                backend="file",
                holder=holder,
                acquired_at=_utc_now_iso(),
                expires_at=_iso_from_epoch(now + float(ttl_sec or 0)),
                _lock_path=lock_path,
                _ttl_sec=int(ttl_sec or 0),
            )
            _start_file_renew_thread(h)
            return h
        except FileExistsError:
            meta = _read_lock_file(lock_path)
            if _is_expired(meta, now_epoch=now):
                # Attempt crash recovery: move aside stale lock and retry.
                try:
                    ts = _utc_now_iso().replace(":", "").replace("-", "")
                    stale = lock_path.with_suffix(lock_path.suffix + f".stale.{ts}.{os.getpid()}")
                    os.replace(str(lock_path), str(stale))
                    continue
                except Exception:
                    # If replace fails, fall through to wait/retry.
                    pass

            if now >= deadline:
                raise LockBusy(lock_key=lock_key, backend="file", holder=meta.get("holder") if isinstance(meta, dict) else {}, waited_sec=time.time() - start)
            time.sleep(max(0.05, float(poll_sec or 0.2)))


def _acquire_db_advisory_lock(
    *,
    lock_key: str,
    dsn: str,
    holder: dict[str, Any],
    wait_sec: float,
    poll_sec: float,
) -> LockHandle:
    start = time.time()
    deadline = start + float(wait_sec or 0)
    key = _advisory_key64(lock_key)
    connected_once = False
    last_err = ""

    while True:
        conn = None
        try:
            conn = _connect_db(dsn)
            connected_once = True
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (key,))
                row = cur.fetchone()
                ok = False
                try:
                    ok = bool((row[0] if isinstance(row, (list, tuple)) else row.get("ok")) if row is not None else False)
                except Exception:
                    ok = False
            if ok:
                # Keep connection open while holding the lock.
                return LockHandle(
                    lock_key=lock_key,
                    backend="db_advisory",
                    holder=holder,
                    acquired_at=_utc_now_iso(),
                    expires_at="",
                    _conn=conn,
                )
            try:
                conn.close()
            except Exception:
                pass
        except Exception as e:
            last_err = str(e)[:200]
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

        if time.time() >= deadline:
            if not connected_once:
                raise DbUnavailable(f"db_unavailable lock_key={lock_key} err={last_err}")
            raise LockBusy(lock_key=lock_key, backend="db_advisory", holder=None, waited_sec=time.time() - start)
        time.sleep(max(0.05, float(poll_sec or 0.2)))


def acquire_repo_lock(
    *,
    repo_root: Optional[Path] = None,
    instance_id: str = "",
    agent_id: str = "",
    task_id: str = "",
    ttl_sec: int = 120,
    wait_sec: float = 30.0,
    poll_sec: float = 0.2,
    prefer_db: bool = True,
) -> LockHandle:
    rr = (repo_root or Path.cwd()).resolve()
    # Keep the DB advisory lock key stable across machines (repo path may differ).
    lock_key = "repo:teamos"
    holder = _default_holder(instance_id=instance_id, agent_id=agent_id, task_id=task_id)

    dsn = _db_dsn()
    if prefer_db and _can_use_db(dsn):
        try:
            return _acquire_db_advisory_lock(lock_key=lock_key, dsn=dsn, holder=holder, wait_sec=wait_sec, poll_sec=poll_sec)
        except LockBusy:
            raise
        except Exception:
            # Fall back to file lock if DB is unavailable.
            pass

    lock_dir = runtime_state_root(override=_runtime_override_for_repo(rr)) / "locks"
    lock_path = lock_dir / "repo.lock"
    return _acquire_file_lock(lock_key=lock_key, lock_path=lock_path, holder=holder, ttl_sec=int(ttl_sec), wait_sec=wait_sec, poll_sec=poll_sec)


def acquire_scope_lock(
    scope: str,
    *,
    repo_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    req_dir: Optional[Path] = None,
    instance_id: str = "",
    agent_id: str = "",
    task_id: str = "",
    ttl_sec: int = 120,
    wait_sec: float = 30.0,
    poll_sec: float = 0.2,
    prefer_db: bool = True,
) -> LockHandle:
    s = str(scope or "").strip()
    if not s:
        raise ValueError("scope is required")
    if s != "teamos" and not s.startswith("project:"):
        s = f"project:{s}"

    rr = (repo_root or Path.cwd()).resolve()
    runtime_override = _runtime_override_for_repo(rr)
    if workspace_root is not None:
        ws = workspace_root.expanduser().resolve()
    else:
        env_ws = str(os.getenv("TEAMOS_WORKSPACE_ROOT") or "").strip()
        ws = Path(env_ws).expanduser().resolve() if env_ws else runtime_workspace_root(override=runtime_override)

    lock_key = f"scope:{s}"
    holder = _default_holder(instance_id=instance_id, agent_id=agent_id, task_id=task_id)

    dsn = _db_dsn()
    if prefer_db and _can_use_db(dsn):
        try:
            return _acquire_db_advisory_lock(lock_key=lock_key, dsn=dsn, holder=holder, wait_sec=wait_sec, poll_sec=poll_sec)
        except LockBusy:
            raise
        except Exception:
            pass

    # File lock path: teamos -> runtime state; project -> workspace project state;
    # fallback -> runtime state locks/fallback (keeps transient lock files outside repo/workspace truth-source roots).
    lock_dir: Path
    if s == "teamos":
        lock_dir = runtime_state_root(override=runtime_override) / "locks"
        lock_name = "scope_teamos.lock"
    else:
        pid = s.split(":", 1)[1].strip() or "unknown"
        lock_dir = ws / "projects" / pid / "state" / "locks"
        lock_name = f"scope_project_{pid}.lock"
        if req_dir is not None and not lock_dir.exists():
            lock_dir = runtime_state_root(override=runtime_override) / "locks" / "fallback"
    lock_path = lock_dir / lock_name
    return _acquire_file_lock(lock_key=lock_key, lock_path=lock_path, holder=holder, ttl_sec=int(ttl_sec), wait_sec=wait_sec, poll_sec=poll_sec)


def acquire_cluster_lock(
    *,
    repo_root: Optional[Path] = None,
    instance_id: str = "",
    agent_id: str = "",
    task_id: str = "",
    ttl_sec: int = 180,
    wait_sec: float = 30.0,
    poll_sec: float = 0.2,
    prefer_db: bool = True,
) -> LockHandle:
    rr = (repo_root or Path.cwd()).resolve()
    lock_key = "cluster:global"
    holder = _default_holder(instance_id=instance_id, agent_id=agent_id, task_id=task_id)

    dsn = _db_dsn()
    if prefer_db and _can_use_db(dsn):
        try:
            return _acquire_db_advisory_lock(lock_key=lock_key, dsn=dsn, holder=holder, wait_sec=wait_sec, poll_sec=poll_sec)
        except LockBusy:
            raise
        except Exception:
            pass

    lock_dir = runtime_state_root(override=_runtime_override_for_repo(rr)) / "locks"
    lock_path = lock_dir / "cluster.lock"
    return _acquire_file_lock(lock_key=lock_key, lock_path=lock_path, holder=holder, ttl_sec=int(ttl_sec), wait_sec=wait_sec, poll_sec=poll_sec)


def acquire_hub_lock(
    *,
    hub_root: Optional[Path] = None,
    instance_id: str = "",
    agent_id: str = "",
    task_id: str = "",
    ttl_sec: int = 180,
    wait_sec: float = 30.0,
    poll_sec: float = 0.2,
    prefer_db: bool = True,
) -> LockHandle:
    hr = (hub_root or runtime_hub_root()).expanduser().resolve()
    lock_key = "hub:global"
    holder = _default_holder(instance_id=instance_id, agent_id=agent_id, task_id=task_id)

    dsn = _db_dsn()
    if prefer_db and _can_use_db(dsn):
        try:
            return _acquire_db_advisory_lock(lock_key=lock_key, dsn=dsn, holder=holder, wait_sec=wait_sec, poll_sec=poll_sec)
        except LockBusy:
            raise
        except Exception:
            pass

    lock_dir = hr / "state" / "locks"
    lock_path = lock_dir / "hub.lock"
    return _acquire_file_lock(lock_key=lock_key, lock_path=lock_path, holder=holder, ttl_sec=int(ttl_sec), wait_sec=wait_sec, poll_sec=poll_sec)


def release_lock(h: Optional[LockHandle]) -> None:
    if h is None:
        return
    h.release()
