#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_IMAGE = "ghcr.io/wangguanran/teamos-control-plane:main"
DEFAULT_INTERVAL_SEC = 300
DEFAULT_PORT = 8787


def _parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        env[str(key).strip()] = str(value).strip()
    return env


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip() or default)
    except Exception:
        return int(default)


def load_runtime_settings(runtime_dir: Path) -> dict[str, Any]:
    env = _parse_env_file(runtime_dir / ".env")
    port = _env_int(env.get("CONTROL_PLANE_PORT"), DEFAULT_PORT)
    return {
        "enabled": _env_bool(env.get("TEAMOS_CONTROL_PLANE_AUTO_UPDATE"), default=False),
        "interval_sec": max(30, _env_int(env.get("TEAMOS_CONTROL_PLANE_AUTO_UPDATE_INTERVAL_SEC"), DEFAULT_INTERVAL_SEC)),
        "only_if_idle": _env_bool(env.get("TEAMOS_CONTROL_PLANE_AUTO_UPDATE_ONLY_IF_IDLE"), default=False),
        "image": str(env.get("TEAMOS_CONTROL_PLANE_IMAGE") or DEFAULT_IMAGE).strip(),
        "port": port,
        "base_url": f"http://127.0.0.1:{port}",
    }


def _run(cmd: list[str], *, cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=check)


def _json_log(**payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _control_plane_container_id(runtime_dir: Path) -> str | None:
    cp = _run(["docker", "compose", "ps", "-q", "control-plane"], cwd=runtime_dir)
    cid = str(cp.stdout or "").strip()
    return cid or None


def local_image_id(image: str) -> str | None:
    cp = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    image_id = str(cp.stdout or "").strip()
    return image_id or None


def current_control_plane_image_id(runtime_dir: Path) -> str | None:
    container_id = _control_plane_container_id(runtime_dir)
    if not container_id:
        return None
    cp = subprocess.run(
        ["docker", "inspect", container_id, "--format", "{{.Image}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    image_id = str(cp.stdout or "").strip()
    return image_id or None


def active_run_count_from_status(payload: dict[str, Any]) -> int:
    runs = payload.get("active_runs")
    if not isinstance(runs, list):
        return 0
    return len(runs)


def query_active_run_count(base_url: str) -> int | None:
    url = base_url.rstrip("/") + "/v1/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return active_run_count_from_status(payload if isinstance(payload, dict) else {})
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError, http.client.HTTPException):
        return None


def should_restart_control_plane(previous_image_id: str | None, updated_image_id: str | None, running_image_id: str | None) -> bool:
    if not updated_image_id:
        return False
    return previous_image_id != updated_image_id or running_image_id != updated_image_id


def run_update_check(runtime_dir: Path) -> dict[str, Any]:
    settings = load_runtime_settings(runtime_dir)
    if not settings["enabled"]:
        return {"status": "disabled", "image": settings["image"]}

    active_runs = query_active_run_count(settings["base_url"])
    if settings["only_if_idle"] and active_runs and active_runs > 0:
        return {
            "status": "skipped_active_runs",
            "active_runs": active_runs,
            "image": settings["image"],
        }

    previous_image_id = local_image_id(settings["image"])
    pull = _run(["docker", "compose", "pull", "control-plane"], cwd=runtime_dir)
    if pull.returncode != 0:
        return {
            "status": "pull_failed",
            "image": settings["image"],
            "stdout": str(pull.stdout or "")[-1200:],
            "stderr": str(pull.stderr or "")[-1200:],
        }

    updated_image_id = local_image_id(settings["image"])
    running_image_id = current_control_plane_image_id(runtime_dir)
    if not should_restart_control_plane(previous_image_id, updated_image_id, running_image_id):
        return {
            "status": "up_to_date",
            "image": settings["image"],
            "image_id": updated_image_id,
            "running_image_id": running_image_id,
        }

    up = _run(
        ["docker", "compose", "up", "-d", "--no-build", "--force-recreate", "--no-deps", "control-plane"],
        cwd=runtime_dir,
    )
    if up.returncode != 0:
        return {
            "status": "restart_failed",
            "image": settings["image"],
            "image_id": updated_image_id,
            "stdout": str(up.stdout or "")[-1200:],
            "stderr": str(up.stderr or "")[-1200:],
        }

    return {
        "status": "updated",
        "image": settings["image"],
        "previous_image_id": previous_image_id,
        "updated_image_id": updated_image_id,
        "running_image_id": current_control_plane_image_id(runtime_dir),
    }


def _state_dir(runtime_dir: Path) -> Path:
    return runtime_dir / "state" / "auto_update"


def _pid_file(runtime_dir: Path) -> Path:
    return _state_dir(runtime_dir) / "watcher.pid"


def _log_file(runtime_dir: Path) -> Path:
    return _state_dir(runtime_dir) / "watcher.log"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def status(runtime_dir: Path) -> int:
    settings = load_runtime_settings(runtime_dir)
    pid_path = _pid_file(runtime_dir)
    watcher_pid: int | None = None
    watcher_running = False
    if pid_path.is_file():
        try:
            watcher_pid = int(pid_path.read_text(encoding="utf-8").strip())
        except Exception:
            watcher_pid = None
        watcher_running = bool(watcher_pid and _pid_alive(watcher_pid))
        if watcher_pid and not watcher_running:
            pid_path.unlink(missing_ok=True)
    _json_log(
        status="status",
        enabled=settings["enabled"],
        interval_sec=settings["interval_sec"],
        only_if_idle=settings["only_if_idle"],
        image=settings["image"],
        watcher_pid=watcher_pid,
        watcher_running=watcher_running,
        log_file=str(_log_file(runtime_dir)),
    )
    return 0


def start(runtime_dir: Path) -> int:
    settings = load_runtime_settings(runtime_dir)
    if not settings["enabled"]:
        _json_log(status="disabled", image=settings["image"], message="auto update disabled in .env")
        return 0
    state_dir = _state_dir(runtime_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_path = _pid_file(runtime_dir)
    log_path = _log_file(runtime_dir)
    if pid_path.is_file():
        try:
            watcher_pid = int(pid_path.read_text(encoding="utf-8").strip())
        except Exception:
            watcher_pid = None
        if watcher_pid and _pid_alive(watcher_pid):
            _json_log(status="already_running", watcher_pid=watcher_pid, log_file=str(log_path))
            return 0
        pid_path.unlink(missing_ok=True)

    with log_path.open("a", encoding="utf-8") as fp:
        proc = subprocess.Popen(
            [sys.executable, __file__, "watch", "--runtime-dir", str(runtime_dir)],
            cwd=str(runtime_dir),
            stdout=fp,
            stderr=fp,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    _json_log(status="started", watcher_pid=proc.pid, log_file=str(log_path))
    return 0


def stop(runtime_dir: Path) -> int:
    pid_path = _pid_file(runtime_dir)
    if not pid_path.is_file():
        _json_log(status="not_running")
        return 0
    try:
        watcher_pid = int(pid_path.read_text(encoding="utf-8").strip())
    except Exception:
        pid_path.unlink(missing_ok=True)
        _json_log(status="not_running")
        return 0

    if _pid_alive(watcher_pid):
        os.kill(watcher_pid, signal.SIGTERM)
    pid_path.unlink(missing_ok=True)
    _json_log(status="stopped", watcher_pid=watcher_pid)
    return 0


def watch(runtime_dir: Path) -> int:
    settings = load_runtime_settings(runtime_dir)
    state_dir = _state_dir(runtime_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    _json_log(status="watch_started", image=settings["image"], interval_sec=settings["interval_sec"])
    try:
        while True:
            result = run_update_check(runtime_dir)
            result["ts"] = int(time.time())
            _json_log(**result)
            settings = load_runtime_settings(runtime_dir)
            time.sleep(settings["interval_sec"])
    except KeyboardInterrupt:
        _json_log(status="watch_stopped")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Watch and update the local TeamOS control-plane image.")
    ap.add_argument("command", choices=["check", "watch", "start", "stop", "status"])
    ap.add_argument("--runtime-dir", default=".", help="Path to the team-os-runtime directory")
    ns = ap.parse_args(argv)

    runtime_dir = Path(ns.runtime_dir).expanduser().resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)

    if ns.command == "check":
        _json_log(**run_update_check(runtime_dir))
        return 0
    if ns.command == "watch":
        return watch(runtime_dir)
    if ns.command == "start":
        return start(runtime_dir)
    if ns.command == "stop":
        return stop(runtime_dir)
    return status(runtime_dir)


if __name__ == "__main__":
    raise SystemExit(main())
