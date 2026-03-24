#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openteam_common import utc_now_iso as _utc_now_iso

_DEFAULT_CREWAI_GIT_URL = "https://github.com/openteam-dev/crewAI.git"
_DEFAULT_CREWAI_GIT_REF = "main"
_DEFAULT_CREWAI_ARCHIVE_URL = "https://codeload.github.com/openteam-dev/crewAI/tar.gz/refs/heads/main"


class BootstrapError(Exception):
    pass


def _repo_root() -> Path:
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if p.returncode != 0:
        raise BootstrapError(f"cannot resolve repo root: {(p.stderr or '').strip()[:300]}")
    return Path((p.stdout or "").strip()).resolve()


def _runtime_root(repo: Path) -> Path:
    v = str(os.getenv("OPENTEAM_RUNTIME_ROOT") or "").strip()
    if v:
        return Path(v).expanduser().resolve()
    home = str(os.getenv("OPENTEAM_HOME") or "").strip()
    if home:
        return (Path(home).expanduser().resolve() / "runtime" / "default").resolve()
    return (Path.home() / ".openteam" / "runtime" / "default").resolve()


def _workspace_root(runtime_root: Path) -> Path:
    v = str(os.getenv("OPENTEAM_WORKSPACE_ROOT") or "").strip()
    if v:
        return Path(v).expanduser().resolve()
    return runtime_root / "workspace"


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def _ensure_runtime_layout(runtime_root: Path) -> None:
    dirs = [
        runtime_root,
        runtime_root / "state",
        runtime_root / "state" / "audit",
        runtime_root / "state" / "logs",
        runtime_root / "state" / "runs",
        runtime_root / "state" / "openteam",
        runtime_root / "state" / "kb" / "sources",
        runtime_root / "workspace",
        runtime_root / "workspace" / "projects",
        runtime_root / "workspace" / "shared" / "cache",
        runtime_root / "workspace" / "shared" / "tmp",
        runtime_root / "workspace" / "config",
        runtime_root / "hub",
        runtime_root / "tmp",
        runtime_root / "cache",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        _chmod_best_effort(d, 0o700)


def _utc_compact() -> str:
    return _utc_now_iso().replace(":", "").replace("-", "")


def _audit_log_path(runtime_root: Path) -> Path:
    return runtime_root / "state" / "audit" / "one_click_bootstrap.log"


def _append_audit(runtime_root: Path, line: str) -> None:
    p = _audit_log_path(runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"[{_utc_now_iso()}] {line.rstrip()}\n")


def _run_json(cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict[str, str]] = None, timeout_sec: int = 300) -> dict[str, Any]:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec, check=False)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        msg = err or out or f"command failed rc={p.returncode}"
        raise BootstrapError(f"{' '.join(cmd)} :: {msg[:600]}")
    if not out:
        return {}
    try:
        obj = json.loads(out)
    except Exception:
        raise BootstrapError(f"expected JSON output from {' '.join(cmd)}; got: {out[:300]}")
    return obj if isinstance(obj, dict) else {}


def _run(cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict[str, str]] = None, timeout_sec: int = 300) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec, check=False)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def _quarantine_legacy_openteam_dir(repo: Path, runtime_root: Path) -> dict[str, Any]:
    legacy = repo / ".openteam"
    if not legacy.exists():
        return {"ok": True, "found": False}
    try:
        next(legacy.iterdir())
    except StopIteration:
        legacy.rmdir()
        return {"ok": True, "found": True, "removed_empty": True}
    except Exception:
        pass
    dst = runtime_root / "state" / "audit" / "legacy_openteam" / _utc_compact()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy), str(dst))
    return {"ok": True, "found": True, "moved_to": str(dst)}


def _venv_python(runtime_root: Path) -> Path:
    if os.name == "nt":
        return runtime_root / "cache" / "py-venv" / "Scripts" / "python.exe"
    return runtime_root / "cache" / "py-venv" / "bin" / "python"


def _crewai_archive_url() -> str:
    explicit = str(os.getenv("OPENTEAM_CREWAI_ARCHIVE_URL") or "").strip()
    if explicit:
        return explicit.split("#", 1)[0]
    git_url = str(os.getenv("OPENTEAM_CREWAI_GIT_URL") or _DEFAULT_CREWAI_GIT_URL).strip()
    git_ref = str(os.getenv("OPENTEAM_CREWAI_GIT_REF") or _DEFAULT_CREWAI_GIT_REF).strip()
    normalized = git_url.removesuffix(".git").rstrip("/")
    prefix = "https://github.com/"
    if normalized.startswith(prefix):
        slug = normalized[len(prefix) :].strip("/")
        if slug:
            return f"https://codeload.github.com/{slug}/tar.gz/refs/heads/{git_ref}"
    return _DEFAULT_CREWAI_ARCHIVE_URL


def _crewai_pip_spec() -> str:
    return f"crewai @ {_crewai_archive_url()}#subdirectory=lib/crewai"


def _missing_python_modules(python_exe: Path) -> list[tuple[str, str]]:
    required: list[tuple[str, str]] = [
        ("uvicorn", "uvicorn"),
        ("fastapi", "fastapi"),
        ("pydantic", "pydantic"),
        ("agents", "openai-agents"),
        ("redis", "redis"),
        ("yaml", "PyYAML"),
        ("psycopg", "psycopg[binary]"),
        ("crewai", _crewai_pip_spec()),
    ]
    code = (
        "import importlib.util,json,sys;"
        "req=json.loads(sys.argv[1]);"
        "miss=[x for x in req if importlib.util.find_spec(x[0]) is None];"
        "print(json.dumps(miss))"
    )
    rc, out, err = _run([str(python_exe), "-c", code, json.dumps(required, ensure_ascii=False)], timeout_sec=60)
    if rc != 0:
        raise BootstrapError(f"failed to inspect python modules ({python_exe}): {(err or out)[:400]}")
    try:
        obj = json.loads((out or "[]").strip() or "[]")
        out_list: list[tuple[str, str]] = []
        for it in obj if isinstance(obj, list) else []:
            if isinstance(it, list) and len(it) == 2:
                out_list.append((str(it[0]), str(it[1])))
        return out_list
    except Exception:
        return required


def _ensure_python_dependencies(runtime_root: Path) -> dict[str, Any]:
    venv_py = _venv_python(runtime_root)
    if not venv_py.exists():
        venv_dir = venv_py.parent.parent
        rc, out, err = _run([sys.executable, "-m", "venv", str(venv_dir)], timeout_sec=300)
        if rc != 0:
            msg = (err or out or "").strip()
            raise BootstrapError(f"failed to create python venv at {venv_dir}: {msg[:800]}")

    missing = _missing_python_modules(venv_py)
    if not missing:
        return {"ok": True, "installed": [], "missing": [], "python": str(venv_py)}

    allow_auto = str(os.getenv("OPENTEAM_AUTO_INSTALL_PY_DEPS", "1") or "").strip().lower() not in ("0", "false", "no", "off")
    if not allow_auto:
        need = sorted(list(set([pkg for _, pkg in missing])))
        raise BootstrapError(
            "missing python dependencies in bootstrap venv: "
            + ", ".join([m for m, _ in missing])
            + " ; install with: "
            + str(venv_py)
            + " -m pip install "
            + " ".join(need)
        )

    pkgs = sorted(list(set([pkg for _, pkg in missing])))
    rc, out, err = _run([str(venv_py), "-m", "pip", "install", *pkgs], timeout_sec=1200)
    if rc != 0:
        msg = (err or out or "").strip()
        raise BootstrapError(f"failed to install python dependencies into bootstrap venv {pkgs}: {msg[:800]}")

    still = _missing_python_modules(venv_py)
    if still:
        raise BootstrapError(f"python dependencies still missing in bootstrap venv: {[m for m, _ in still]}")

    _append_audit(runtime_root, f"python deps installed in bootstrap venv: {' '.join(pkgs)}")
    return {"ok": True, "installed": pkgs, "missing_before": [m for m, _ in missing], "python": str(venv_py)}


def _http_json(method: str, url: str, payload: Optional[dict[str, Any]] = None, timeout_sec: int = 5) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise BootstrapError(f"HTTP {e.code} {e.reason} {url}: {body[:400]}") from e
    except Exception as e:
        raise BootstrapError(f"HTTP request failed {url}: {e}") from e


def _pid_path(runtime_root: Path, name: str) -> Path:
    return runtime_root / "state" / "runs" / f"{name}.pid"


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip()) if path.exists() else 0
    except Exception:
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _stop_pid(path: Path, *, grace_sec: float = 10.0) -> dict[str, Any]:
    pid = _read_pid(path)
    if pid <= 0:
        return {"ok": True, "stopped": False, "reason": "no_pid_file"}
    if not _pid_alive(pid):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": True, "stopped": True, "pid": pid, "reason": "not_running"}

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception as e:
        return {"ok": False, "stopped": False, "pid": pid, "error": str(e)[:200]}

    deadline = time.time() + grace_sec
    while time.time() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.2)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    stopped = not _pid_alive(pid)
    if stopped:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True, "stopped": stopped, "pid": pid}


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[str(k).strip()] = str(v).strip()
    return out


def _db_url_from_hub_env(env: dict[str, str]) -> str:
    u = str(env.get("POSTGRES_USER") or "openteam")
    p = str(env.get("POSTGRES_PASSWORD") or "")
    h = str(env.get("PG_BIND_IP") or "127.0.0.1")
    pt = str(env.get("PG_PORT") or "5432")
    db = str(env.get("POSTGRES_DB") or "openteam")
    return f"postgresql://{u}:{p}@{h}:{pt}/{db}"


def _redis_url_from_hub_env(env: dict[str, str]) -> str:
    p = str(env.get("REDIS_PASSWORD") or "")
    h = str(env.get("REDIS_BIND_IP") or "127.0.0.1")
    pt = str(env.get("REDIS_PORT") or "6379")
    return f"redis://:{p}@{h}:{pt}/0"


def _mask_secret(v: str) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}***{s[-4:]}"


def _codex_login_status() -> tuple[bool, str]:
    try:
        p = subprocess.run(
            ["codex", "login", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return False, "codex CLI not found in PATH"
    except Exception as e:
        return False, f"codex login status failed: {e}"

    msg = (p.stdout or "").strip() or (p.stderr or "").strip()
    return p.returncode == 0, (msg or f"exit_code={p.returncode}")


def _llm_config() -> dict[str, Any]:
    base = str(os.getenv("OPENTEAM_LLM_BASE_URL") or "").strip()
    key = str(os.getenv("OPENTEAM_LLM_API_KEY") or "").strip()
    model = str(os.getenv("OPENTEAM_LLM_MODEL") or "openai/gpt-5.4").strip()
    needs_codex = "codex" in model.lower()
    codex_logged_in, codex_login_message = _codex_login_status()
    codex_oauth_ready = bool(needs_codex and codex_logged_in)
    api_key_ready = bool(base and key)
    ok = bool(api_key_ready or codex_oauth_ready)
    auth_strategy = ""
    if codex_oauth_ready:
        auth_strategy = "codex_oauth"
    elif api_key_ready:
        auth_strategy = "api_key"
    return {
        "ok": ok,
        "model": model,
        "base_url": base,
        "api_key_masked": _mask_secret(key),
        "auth_strategy": auth_strategy,
        "codex_login_status": codex_login_message,
        "codex_oauth_ready": codex_oauth_ready,
        "required": [
            "Codex OAuth login via `codex login` for codex models",
            "or OPENTEAM_LLM_BASE_URL + OPENTEAM_LLM_API_KEY",
        ],
    }


def _require_llm_config(runtime_root: Path) -> dict[str, Any]:
    cfg = _llm_config()
    if not bool(cfg.get("ok")):
        raise BootstrapError(
            "missing required LLM config: either run `codex login` for codex models, "
            "or set OPENTEAM_LLM_BASE_URL and OPENTEAM_LLM_API_KEY"
        )
    _append_audit(
        runtime_root,
        "llm config ready "
        f"strategy={cfg.get('auth_strategy') or 'unknown'} "
        f"model={cfg.get('model') or ''} "
        f"base_url={cfg.get('base_url')} "
        f"api_key={cfg.get('api_key_masked')} "
        f"codex_login={cfg.get('codex_login_status') or ''}",
    )
    return cfg


def _wait_hub_healthy(repo: Path, workspace_root: Path, *, timeout_sec: int = 90) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        out = _run_json([sys.executable, str(repo / "scripts" / "pipelines" / "hub_status.py"), "--repo-root", str(repo), "--workspace-root", str(workspace_root)])
        last = out
        pg = bool(((out.get("postgres") or {}).get("tcp_open")))
        rd = bool(((out.get("redis") or {}).get("tcp_open")))
        if pg and rd:
            return out
        time.sleep(2)
    raise BootstrapError(f"hub health timeout: {json.dumps(last, ensure_ascii=False)[:600]}")


def _wait_http_ready(base_url: str, *, timeout_sec: int = 120) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            hz = _http_json("GET", base_url + "/healthz", None, timeout_sec=3)
            st = _http_json("GET", base_url + "/v1/status", None, timeout_sec=3)
            if str(hz.get("status") or "").lower() in ("ok", "healthy") or hz:
                return {"ok": True, "healthz": hz, "status": st}
        except Exception as e:
            last_error = str(e)[:300]
        time.sleep(1)
    raise BootstrapError(f"control plane not ready at {base_url}: {last_error}")


def _start_control_plane(
    repo: Path,
    runtime_root: Path,
    workspace_root: Path,
    *,
    base_url: str,
    port: int,
    db_url: str,
    redis_url: str,
    python_exec: str,
) -> dict[str, Any]:
    pidp = _pid_path(runtime_root, "control_plane")
    existing = _read_pid(pidp)
    if _pid_alive(existing):
        try:
            _wait_http_ready(base_url, timeout_sec=5)
            return {"ok": True, "already_running": True, "pid": existing}
        except Exception:
            _stop_pid(pidp, grace_sec=3)

    orch_dir = (repo / "scaffolds" / "runtime" / "orchestrator").resolve()
    if not orch_dir.exists():
        raise BootstrapError(f"missing orchestrator dir: {orch_dir}")

    logs_dir = runtime_root / "state" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "control_plane.log"
    logf = log_path.open("a", encoding="utf-8")

    env = os.environ.copy()
    env["OPENTEAM_REPO_PATH"] = str(repo)
    env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)
    env["OPENTEAM_WORKSPACE_ROOT"] = str(workspace_root)
    env["OPENTEAM_DB_URL"] = db_url
    env["OPENTEAM_REDIS_URL"] = redis_url
    env["OPENTEAM_RUNTIME_DB_PATH"] = str(runtime_root / "state" / "runtime.db")
    env["OPENTEAM_BASE_URL"] = base_url
    env["CONTROL_PLANE_BASE_URL"] = base_url
    env["OPENTEAM_PIPELINE_PYTHON"] = str(sys.executable)
    env["OPENTEAM_RUNTIME_WORKFLOW_LOOPS_ENABLED"] = "0"
    env.setdefault("CREWAI_TRACING_ENABLED", "false")
    py_path = str(orch_dir)
    if str(env.get("PYTHONPATH") or "").strip():
        py_path = py_path + os.pathsep + str(env.get("PYTHONPATH"))
    env["PYTHONPATH"] = py_path

    cmd = [str(python_exec), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(int(port))]
    p = subprocess.Popen(cmd, cwd=str(orch_dir), stdout=logf, stderr=logf, env=env, start_new_session=True)
    pidp.parent.mkdir(parents=True, exist_ok=True)
    pidp.write_text(str(int(p.pid)) + "\n", encoding="utf-8")

    try:
        _wait_http_ready(base_url, timeout_sec=120)
    except Exception:
        _stop_pid(pidp, grace_sec=1.0)
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            hint = "\\n".join(tail)
        except Exception:
            hint = ""
        if hint:
            raise BootstrapError(f"control plane failed to start; recent log tail:\\n{hint}")
        raise
    return {"ok": True, "already_running": False, "pid": int(p.pid), "log_path": str(log_path)}


def _ensure_crewai_ready(base_url: str) -> dict[str, Any]:
    runs = _http_json("GET", base_url + "/v1/runs", None, timeout_sec=5)
    agents = _http_json("GET", base_url + "/v1/agents", None, timeout_sec=5)
    return {
        "ok": True,
        "runs_endpoint": True,
        "agents_endpoint": True,
        "runs_count": len((runs.get("runs") or [])) if isinstance(runs, dict) else 0,
        "agents_count": len((agents.get("agents") or [])) if isinstance(agents, dict) else 0,
    }


def _default_team_id(base_url: str) -> str:
    teams = _http_json("GET", base_url + "/v1/teams", None, timeout_sec=5)
    items = list(teams.get("teams") or []) if isinstance(teams, dict) else []
    for item in items:
        team_id = str(item.get("team_id") or "").strip()
        if team_id:
            return team_id
    raise BootstrapError("no configured teams found in control plane")


def _read_default_team_state(runtime_root: Path, *, base_url: str = "") -> dict[str, Any]:
    _ = runtime_root
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return {}
    try:
        status = _http_json("GET", url + "/v1/status", None, timeout_sec=5)
    except Exception:
        return {}
    team_id = str(status.get("default_team_id") or "").strip()
    teams = status.get("teams") if isinstance(status, dict) else {}
    if not team_id and isinstance(teams, dict) and teams:
        team_id = sorted(str(key) for key in teams.keys() if str(key).strip())[0]
    if not team_id or not isinstance(teams, dict):
        return {}
    team_state = teams.get(team_id)
    return dict(team_state) if isinstance(team_state, dict) else {}


def _run_default_team_bootstrap(repo: Path, base_url: str) -> dict[str, Any]:
    team_id = _default_team_id(base_url)
    out = _http_json(
        "POST",
        base_url + f"/v1/teams/{team_id}/run",
        {
            "project_id": "openteam",
            "workstream_id": "general",
            "repo_path": str(repo),
            "objective": f"Bootstrap team:{team_id} for current repository",
            "dry_run": False,
            "force": True,
            "trigger": "bootstrap",
        },
        timeout_sec=900,
    )
    if not bool(out.get("ok")):
        raise BootstrapError(f"team bootstrap run failed: {json.dumps(out, ensure_ascii=False)[:600]}")
    return out


def _resume_tasks(base_url: str) -> dict[str, Any]:
    return _http_json("POST", base_url + "/v1/recovery/resume", {"all": True}, timeout_sec=60)


def _check_repo_purity(repo: Path, workspace_root: Path) -> dict[str, Any]:
    out = _run_json(
        [
            sys.executable,
            str(repo / "scripts" / "pipelines" / "repo_purity_check.py"),
            "--repo-root",
            str(repo),
            "--workspace-root",
            str(workspace_root),
            "--json",
        ],
        cwd=repo,
        timeout_sec=60,
    )
    if not bool(out.get("ok")):
        raise BootstrapError(f"repo purity failed: {json.dumps(out, ensure_ascii=False)[:600]}")
    return out


def _status_snapshot(repo: Path, runtime_root: Path, workspace_root: Path, base_url: str) -> dict[str, Any]:
    cp_pid = _read_pid(_pid_path(runtime_root, "control_plane"))
    cp_running = _pid_alive(cp_pid)

    control: dict[str, Any] = {"running": cp_running, "pid": cp_pid, "base_url": base_url}
    if cp_running:
        try:
            control["healthz"] = _http_json("GET", base_url + "/healthz", None, timeout_sec=3)
            control["status"] = _http_json("GET", base_url + "/v1/status", None, timeout_sec=3)
        except Exception as e:
            control["health_error"] = str(e)[:300]

    hub_status: dict[str, Any] = {}
    try:
        hub_status = _run_json([sys.executable, str(repo / "scripts" / "pipelines" / "hub_status.py"), "--repo-root", str(repo), "--workspace-root", str(workspace_root)], cwd=repo)
    except Exception as e:
        hub_status = {"ok": False, "error": str(e)[:300]}

    team_state = _read_default_team_state(runtime_root, base_url=base_url)
    team_last = (
        (team_state.get("last_run") or {})
        if isinstance(team_state.get("last_run"), dict)
        else {}
    )

    return {
        "ok": True,
        "repo_root": str(repo),
        "runtime_root": str(runtime_root),
        "workspace_root": str(workspace_root),
        "llm": _llm_config(),
        "hub": hub_status,
        "control_plane": control,
        "default_team": {
            "last_run": team_last,
            "state_backend": "control_plane_status",
        },
    }


def _start_flow(repo: Path, runtime_root: Path, workspace_root: Path, *, port: int) -> dict[str, Any]:
    base_url = f"http://127.0.0.1:{int(port)}"
    _append_audit(runtime_root, "bootstrap start")

    # 1) purity
    purity = _check_repo_purity(repo, workspace_root)
    _append_audit(runtime_root, "repo purity check passed")

    # 2) runtime dirs
    _ensure_runtime_layout(runtime_root)
    _append_audit(runtime_root, "runtime layout ensured")

    # 2.5) required LLM config (base_url + api_key)
    llm_cfg = _require_llm_config(runtime_root)
    _append_audit(runtime_root, "llm config check passed")

    # 3) hub init
    hub_init = _run_json([sys.executable, str(repo / "scripts" / "pipelines" / "hub_init.py"), "--repo-root", str(repo), "--workspace-root", str(workspace_root)], cwd=repo)
    _append_audit(runtime_root, "hub init completed")

    # 4) hub up
    hub_up = _run_json([sys.executable, str(repo / "scripts" / "pipelines" / "hub_up.py"), "--repo-root", str(repo), "--workspace-root", str(workspace_root)], cwd=repo, timeout_sec=180)
    _append_audit(runtime_root, "hub up completed")

    # 5) hub health
    hub_health = _wait_hub_healthy(repo, workspace_root, timeout_sec=120)
    _append_audit(runtime_root, "hub health check passed")

    # 6) migrations
    hub_migrate = _run_json([sys.executable, str(repo / "scripts" / "pipelines" / "hub_migrate.py"), "--repo-root", str(repo), "--workspace-root", str(workspace_root)], cwd=repo, timeout_sec=180)
    _append_audit(runtime_root, "hub migrations completed")

    # 6.5) python deps for control plane/orchestrator runtime
    python_deps = _ensure_python_dependencies(runtime_root)
    _append_audit(runtime_root, "python dependencies ready")
    control_python = str(python_deps.get("python") or sys.executable)

    # Build DB/Redis URLs from hub env for control plane runtime.
    hub_env = _parse_env_file(runtime_root / "hub" / "env" / ".env")
    db_url = _db_url_from_hub_env(hub_env)
    redis_url = _redis_url_from_hub_env(hub_env)
    os.environ["OPENTEAM_DB_URL"] = db_url
    os.environ["OPENTEAM_REDIS_URL"] = redis_url
    os.environ["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["OPENTEAM_WORKSPACE_ROOT"] = str(workspace_root)

    # 7) control plane
    control_plane = _start_control_plane(
        repo,
        runtime_root,
        workspace_root,
        base_url=base_url,
        port=port,
        db_url=db_url,
        redis_url=redis_url,
        python_exec=control_python,
    )
    _append_audit(runtime_root, "control plane ready")

    # 8) crewai orchestrator readiness
    crew_ready = _ensure_crewai_ready(base_url)
    _append_audit(runtime_root, "crewai orchestrator readiness check passed")

    # 9) force one bootstrap team run (must actually execute)
    team_bootstrap = _run_default_team_bootstrap(repo, base_url)
    _append_audit(runtime_root, "default team bootstrap run executed")

    # hard check: last_run must exist after bootstrap
    st = _read_default_team_state(runtime_root, base_url=base_url)
    last_run = (st.get("last_run") or {}) if isinstance(st, dict) else {}
    if not str(last_run.get("ts") or "").strip():
        raise BootstrapError("team bootstrap not persisted: missing last_run.ts")

    # 10) resume unfinished tasks
    recovered = _resume_tasks(base_url)
    _append_audit(runtime_root, "recovery resume executed")

    # 11) final summary
    summary = _status_snapshot(repo, runtime_root, workspace_root, base_url)
    summary.update(
        {
            "startup": {
                "purity": purity,
                "llm": llm_cfg,
                "hub_init": hub_init,
                "hub_up": hub_up,
                "hub_health": hub_health,
                "hub_migrate": hub_migrate,
                "python_dependencies": python_deps,
                "control_plane": control_plane,
                "crewai_ready": crew_ready,
                "team_bootstrap": team_bootstrap,
                "recovery": recovered,
            }
        }
    )
    _append_audit(runtime_root, "bootstrap completed")
    return summary


def _stop_flow(repo: Path, runtime_root: Path, workspace_root: Path, *, keep_hub: bool = False) -> dict[str, Any]:
    _append_audit(runtime_root, "stop start")

    cp_stop = _stop_pid(_pid_path(runtime_root, "control_plane"))

    hub_down: dict[str, Any] = {"ok": True, "skipped": keep_hub}
    if not keep_hub:
        try:
            hub_down = _run_json([sys.executable, str(repo / "scripts" / "pipelines" / "hub_down.py"), "--repo-root", str(repo), "--workspace-root", str(workspace_root)], cwd=repo, timeout_sec=180)
        except Exception as e:
            hub_down = {"ok": False, "error": str(e)[:300]}

    _append_audit(runtime_root, "stop completed")
    return {"ok": True, "default_team": {"ok": True, "mode": "no_daemon"}, "control_plane": cp_stop, "hub": hub_down}


def _doctor(repo: Path, workspace_root: Path) -> dict[str, Any]:
    return _run_json(
        [
            sys.executable,
            str(repo / "scripts" / "pipelines" / "doctor.py"),
            "--repo-root",
            str(repo),
            "--workspace-root",
            str(workspace_root),
            "--json",
        ],
        cwd=repo,
        timeout_sec=180,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Team-OS one-click bootstrap and runtime controller")
    ap.add_argument("action", nargs="?", default="start", choices=["start", "status", "stop", "restart", "doctor"])
    ap.add_argument("--control-plane-port", type=int, default=int(os.getenv("OPENTEAM_CONTROL_PLANE_PORT") or "8787"))
    ap.add_argument("--keep-hub", action="store_true", help="used with stop: keep hub running")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = _repo_root()
    runtime_root = _runtime_root(repo)
    workspace_root = _workspace_root(runtime_root)

    # secrets/runtime safety default
    os.umask(0o077)
    _ensure_runtime_layout(runtime_root)

    try:
        if args.action == "start":
            legacy_dir = _quarantine_legacy_openteam_dir(repo, runtime_root)
            if legacy_dir.get("found"):
                _append_audit(runtime_root, f"quarantined legacy .openteam dir -> {legacy_dir.get('moved_to')}")
            out = _start_flow(repo, runtime_root, workspace_root, port=int(args.control_plane_port))
        elif args.action == "status":
            base_url = f"http://127.0.0.1:{int(args.control_plane_port)}"
            out = _status_snapshot(repo, runtime_root, workspace_root, base_url)
        elif args.action == "stop":
            out = _stop_flow(repo, runtime_root, workspace_root, keep_hub=bool(args.keep_hub))
        elif args.action == "restart":
            _ = _stop_flow(repo, runtime_root, workspace_root, keep_hub=False)
            legacy_dir = _quarantine_legacy_openteam_dir(repo, runtime_root)
            if legacy_dir.get("found"):
                _append_audit(runtime_root, f"quarantined legacy .openteam dir -> {legacy_dir.get('moved_to')}")
            out = _start_flow(repo, runtime_root, workspace_root, port=int(args.control_plane_port))
        else:
            base_url = f"http://127.0.0.1:{int(args.control_plane_port)}"
            out = {
                "ok": True,
                "doctor": _doctor(repo, workspace_root),
                "status": _status_snapshot(repo, runtime_root, workspace_root, base_url),
            }
    except BootstrapError as e:
        err = {
            "ok": False,
            "error": str(e),
            "repo_root": str(repo),
            "runtime_root": str(runtime_root),
            "workspace_root": str(workspace_root),
            "audit_log": str(_audit_log_path(runtime_root)),
        }
        _append_audit(runtime_root, f"ERROR {str(e)}")
        if args.json:
            print(json.dumps(err, ensure_ascii=False, indent=2))
        else:
            for k, v in err.items():
                print(f"  {k}: {v}")
        return 2

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for k, v in out.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
