#!/usr/bin/env python3
import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


try:
    import tomllib as tomli  # type: ignore
except Exception:  # pragma: no cover
    try:
        import tomli  # type: ignore
    except Exception:  # pragma: no cover
        tomli = None


CONFIG_DIR = Path.home() / ".openteam"
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_WORKSPACE_ROOT = Path.home() / ".openteam" / "workspace"

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)


def _http_json(
    method: str,
    url: str,
    payload: Optional[dict[str, Any]] = None,
    timeout_sec: int = 10,
    *,
    _redirect_depth: int = 0,
) -> dict[str, Any]:
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
        # Leader-only writes: auto-forward to Brain when server returns 409 with leader info.
        if e.code == 409 and _redirect_depth < 1:
            try:
                j = json.loads(body) if body else {}
            except Exception:
                j = {}
            leader_base = ""
            if isinstance(j, dict):
                leader_base = str(j.get("leader_base_url") or "").strip()
                if not leader_base and isinstance(j.get("detail"), dict):
                    leader_base = str((j.get("detail") or {}).get("leader_base_url") or "").strip()
            if leader_base:
                p = urllib.parse.urlparse(url)
                leader_base = leader_base.rstrip("/")
                new_url = leader_base + p.path
                if p.query:
                    new_url += "?" + p.query
                return _http_json(method, new_url, payload, timeout_sec, _redirect_depth=_redirect_depth + 1)
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body[:2000]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP request failed: {e}") from e


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _iter_sse_events(resp: Any):
    event_type = "message"
    data_lines: list[str] = []
    event_id = ""
    while True:
        raw = resp.readline()
        if not raw:
            if data_lines:
                payload_text = "\n".join(data_lines)
                yield {
                    "event": event_type,
                    "id": event_id,
                    "data": _safe_json_loads(payload_text) if payload_text else {},
                }
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                payload_text = "\n".join(data_lines)
                yield {
                    "event": event_type,
                    "id": event_id,
                    "data": _safe_json_loads(payload_text) if payload_text else {},
                }
            event_type = "message"
            data_lines = []
            event_id = ""
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip() or "message"
            continue
        if line.startswith("id:"):
            event_id = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    if tomli is None:
        raise RuntimeError("Missing dependency: tomllib/tomli (install: python3 -m pip install --user tomli)")
    with CONFIG_PATH.open("rb") as f:
        return tomli.load(f) or {}


def _dump_toml(cfg: dict[str, Any]) -> str:
    cur = cfg.get("current_profile", "")
    profiles = cfg.get("profiles", {}) or {}
    out: list[str] = []
    if cur:
        out.append(f'current_profile = "{cur}"')
        out.append("")
    # Workspace (local filesystem). This is user config (never committed).
    if cfg.get("workspace_root"):
        out.append(f'workspace_root = "{cfg.get("workspace_root")}"')
        out.append("")
    if cfg.get("default_project_id"):
        out.append(f'default_project_id = "{cfg.get("default_project_id")}"')
        out.append("")
    if "leader_only_writes" in cfg:
        v = bool(cfg.get("leader_only_writes"))
        out.append(f"leader_only_writes = {'true' if v else 'false'}")
        out.append("")
    for name, p in profiles.items():
        out.append(f"[profiles.{name}]")
        out.append(f'base_url = "{p.get("base_url","")}"')
        if p.get("default_project_id"):
            out.append(f'default_project_id = "{p.get("default_project_id")}"')
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _save_config(cfg: dict[str, Any]) -> None:
    _ensure_config_dir()
    CONFIG_PATH.write_text(_dump_toml(cfg), encoding="utf-8")


def _get_profile(cfg: dict[str, Any], name: Optional[str]) -> dict[str, Any]:
    profiles = cfg.get("profiles", {}) or {}
    if name:
        if name not in profiles:
            raise RuntimeError(f"Unknown profile: {name}. Use: openteam config show")
        return {"name": name, **profiles[name]}
    cur = cfg.get("current_profile")
    if cur and cur in profiles:
        return {"name": cur, **profiles[cur]}
    if "local" in profiles:
        return {"name": "local", **profiles["local"]}
    raise RuntimeError("No profile configured. Run: openteam config init")


def _fmt_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    sep = "  "
    lines = []
    lines.append(sep.join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    lines.append(sep.join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        lines.append(sep.join(r[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(lines)


def _norm(s: Any) -> str:
    return str(s or "").strip()


def _display_task_state(state: Any) -> str:
    """
    Human-facing task state label.

    Notes:
    - Ledger historically used "running" for in-progress. We standardize the display as "doing".
    - Keep this mapping in CLI to avoid rewriting legacy ledgers.
    """
    s = _norm(state).lower()
    if s in ("running", "work", "in_progress", "inprogress", "doing"):
        return "doing"
    if s in ("waitingpm", "wait_pm", "waitpm"):
        return "waiting_pm"
    return s


def _agent_is_active(agent_state: Any) -> bool:
    s = _norm(agent_state).upper()
    return bool(s) and s not in ("IDLE", "DONE", "FAILED", "STOPPED", "CANCELLED")


def _looks_like_openteam_repo(root: Path) -> bool:
    return (root / "scripts" / "pipelines").exists() and ((root / "openteam").exists() or (root / "OPENTEAM.md").exists())


def _find_openteam_repo_root() -> Optional[Path]:
    """
    Best-effort OpenTeam repo root detection.
    Priority:
    1) env OPENTEAM_REPO_PATH
    2) this script location or parents containing AGENTS + scripts/pipelines
    3) current directory or parents containing AGENTS + scripts/pipelines
    """
    env = (os.getenv("OPENTEAM_REPO_PATH") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if _looks_like_openteam_repo(p):
            return p

    # When invoked by absolute path from another repo, detect relative to this file.
    try:
        here = Path(__file__).resolve()
        for p in [here.parent] + list(here.parents):
            if _looks_like_openteam_repo(p):
                return p
    except Exception:
        pass

    cur = Path.cwd().resolve()
    for p in [cur] + list(cur.parents):
        if _looks_like_openteam_repo(p):
            return p
    return None


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _run_pipeline(repo_root: Path, script_rel: str, argv: list[str], *, env: Optional[dict[str, str]] = None) -> None:
    """
    Run a deterministic pipeline script from this repo.

    Pipelines are the only allowed writers for truth-source artifacts.
    """
    script = (repo_root / script_rel).resolve()
    if not script.exists():
        raise RuntimeError(f"missing pipeline: {script}")
    cmd = [sys.executable, str(script)] + list(argv or [])
    p = subprocess.run(cmd, check=False, env=env or None)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def _run_pipeline_capture(repo_root: Path, script_rel: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """
    Run a pipeline and capture stdout/stderr for downstream parsing.
    """
    script = (repo_root / script_rel).resolve()
    if not script.exists():
        raise RuntimeError(f"missing pipeline: {script}")
    cmd = [sys.executable, str(script)] + list(argv or [])
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if p.returncode != 0:
        sys.stdout.write(p.stdout or "")
        sys.stderr.write(p.stderr or "")
        raise SystemExit(p.returncode)
    return p


def _sanitize_installer_excerpt(text: str, *, max_chars: int = 2000) -> str:
    s = str(text or "")
    if len(s) > max_chars:
        s = s[-max_chars:]
    s = re.sub(r"(postgres(?:ql)?://[^:\s/]+:)([^@/\s]+)(@)", r"\1***\3", s, flags=re.IGNORECASE)
    s = re.sub(r"(redis://:)([^@/\s]+)(@)", r"\1***\3", s, flags=re.IGNORECASE)
    s = re.sub(r"((?:password|passwd|token|secret|api[_-]?key)\s*[=:]\s*)([^\s]+)", r"\1***", s, flags=re.IGNORECASE)
    return s


def _extract_stage_from_json_output(text: str, *, default: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return default
    try:
        obj = json.loads(raw)
    except Exception:
        return default
    if isinstance(obj, dict):
        stage = str(obj.get("stage") or "").strip()
        if stage:
            return stage
    return default


def _record_installer_run(
    *,
    repo_root: Path,
    workspace_root: Path,
    component: str,
    stage: str,
    target_host: str,
    ok: bool,
    stdout_text: str,
    stderr_text: str,
) -> None:
    script = (repo_root / "scripts" / "pipelines" / "installer_failure_classifier.py").resolve()
    if not script.exists():
        eprint(f"warning: installer classifier missing: {script}")
        return
    payload = {
        "component": str(component),
        "stage": str(stage),
        "target_host": str(target_host),
        "ok": bool(ok),
        "stdout": _sanitize_installer_excerpt(stdout_text),
        "stderr": _sanitize_installer_excerpt(stderr_text),
    }
    cmd = [
        sys.executable,
        str(script),
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(workspace_root),
        "--input-json",
        "-",
        "record",
    ]
    p = subprocess.run(
        cmd,
        input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        eprint("warning: failed to record installer run")
        if p.stdout:
            eprint((p.stdout or "").strip()[-800:])
        if p.stderr:
            eprint((p.stderr or "").strip()[-800:])


def _infer_task_id_from_branch(repo_root: Path) -> str:
    """
    Best-effort: infer task id for audit/approvals correlation.

    Priority:
    1) `OPENTEAM_TASK_ID` env (supports branchless workflows)
    2) git branch name patterns (legacy)
    """
    env_tid = str(os.getenv("OPENTEAM_TASK_ID") or "").strip()
    if env_tid:
        return env_tid
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if p.returncode != 0:
            return ""
        b = (p.stdout or "").strip()
        m = re.match(r"^openteam/((?:OPENTEAM-[0-9]{4})|(?:TASK-[0-9]{8}-[0-9]{6}))-", b)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _approval_gate(
    args: argparse.Namespace,
    *,
    repo_root: Path,
    action_kind: str,
    summary: str,
    payload: Optional[dict[str, Any]] = None,
    yes: bool = False,
) -> None:
    """
    Gate a HIGH risk action via approvals pipeline.
    - In single-machine mode this will prompt (unless yes=True).
    - In leader mode this may auto-approve based on policy.
    """
    payload = payload or {}
    task_id = _infer_task_id_from_branch(repo_root)
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--json",
        "request",
        "--task-id",
        task_id,
        "--action-kind",
        str(action_kind),
        "--summary",
        str(summary),
        "--payload-json",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    ]
    if yes:
        argv.append("--yes")
    else:
        argv.append("--interactive")
    # Pipeline prints JSON and exits non-zero when not approved.
    _run_pipeline(repo_root, "scripts/pipelines/approvals.py", argv)


def _workspace_root_from_cfg(cfg: dict[str, Any]) -> Path:
    v = str(cfg.get("workspace_root") or "").strip()
    if not v:
        v = str(os.getenv("OPENTEAM_WORKSPACE_ROOT") or "").strip()
    if not v:
        v = str(DEFAULT_WORKSPACE_ROOT)
    return Path(v).expanduser().resolve()


def _workspace_root(args: argparse.Namespace) -> Path:
    v = str(getattr(args, "workspace_root", "") or "").strip()
    if v:
        return Path(v).expanduser().resolve()
    cfg = _load_config()
    return _workspace_root_from_cfg(cfg)


def _workspace_project_dir(workspace_root: Path, project_id: str) -> Path:
    pid = _norm(project_id)
    if not pid:
        raise RuntimeError("project_id is required")
    return workspace_root / "projects" / pid


def _is_safe_project_id(pid: str) -> bool:
    s = _norm(pid)
    return bool(s) and (s == s.lower()) and bool(_PROJECT_ID_RE.match(s))


def _ensure_project_scaffold(workspace_root: Path, project_id: str) -> None:
    """
    Ensure per-project Workspace structure exists (idempotent).

    This mirrors control-plane expectations:
      <WORKSPACE>/projects/<id>/{repo,state/...}
    """
    pid = _norm(project_id)
    if not _is_safe_project_id(pid):
        # Don't create unsafe paths; doctor will fail and user can rename.
        return

    pdir = workspace_root / "projects" / pid
    repo = pdir / "repo"
    state = pdir / "state"

    (repo).mkdir(parents=True, exist_ok=True)
    (state / "ledger" / "tasks").mkdir(parents=True, exist_ok=True)
    (state / "ledger" / "conversations" / pid).mkdir(parents=True, exist_ok=True)
    (state / "logs" / "tasks").mkdir(parents=True, exist_ok=True)
    (state / "requirements" / "conflicts").mkdir(parents=True, exist_ok=True)
    (state / "requirements" / "baseline").mkdir(parents=True, exist_ok=True)
    raw_inputs = state / "requirements" / "raw_inputs.jsonl"
    if not raw_inputs.exists():
        raw_inputs.write_text("", encoding="utf-8")
    (state / "prompts").mkdir(parents=True, exist_ok=True)
    (state / "plan").mkdir(parents=True, exist_ok=True)
    (state / "kb").mkdir(parents=True, exist_ok=True)
    (state / "cluster").mkdir(parents=True, exist_ok=True)

    mp = state / "prompts" / "MASTER_PROMPT.md"
    if not mp.exists():
        mp.write_text(f"# MASTER PROMPT ({pid})\n\n- TODO\n", encoding="utf-8")

    plan_yaml = state / "plan" / "plan.yaml"
    if not plan_yaml.exists():
        plan_yaml.write_text(f"schema_version: 1\nproject_id: \"{pid}\"\nmilestones: []\n", encoding="utf-8")
    plan_md = state / "plan" / "PLAN.md"
    if not plan_md.exists():
        plan_md.write_text(f"# PLAN ({pid})\n\n- TODO\n", encoding="utf-8")

    req_yaml = state / "requirements" / "requirements.yaml"
    if not req_yaml.exists():
        req_yaml.write_text(
            "\n".join(
                [
                    "schema_version: 1",
                    f"project_id: {pid}",
                    "next_req_seq: 1",
                    "requirements: []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    req_md = state / "requirements" / "REQUIREMENTS.md"
    if not req_md.exists():
        req_md.write_text(f"# Requirements ({pid})\n\n", encoding="utf-8")
    ch = state / "requirements" / "CHANGELOG.md"
    if not ch.exists():
        ch.write_text(f"# Requirements Changelog ({pid})\n\n", encoding="utf-8")


def _ensure_workspace_scaffold(workspace_root: Path) -> None:
    # Idempotent.
    (workspace_root / "projects").mkdir(parents=True, exist_ok=True)
    (workspace_root / "shared" / "cache").mkdir(parents=True, exist_ok=True)
    (workspace_root / "shared" / "tmp").mkdir(parents=True, exist_ok=True)
    (workspace_root / "config").mkdir(parents=True, exist_ok=True)
    cfg = workspace_root / "config" / "workspace.toml"
    if not cfg.exists():
        cfg.write_text(
            "\n".join(
                [
                    "# OpenTeam Workspace config (local; not committed)",
                    "",
                    f'workspace_root = "{workspace_root}"',
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    # Repair: ensure existing projects have required per-project subdirs.
    projects_dir = workspace_root / "projects"
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir()):
            if d.is_dir():
                _ensure_project_scaffold(workspace_root, d.name)


def cmd_workspace_init(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser().resolve() if getattr(args, "path", "") else _workspace_root(args)
    _ensure_workspace_scaffold(path)
    print(f"workspace_root={path}")
    print("workspace_init: OK")


def cmd_workspace_show(args: argparse.Namespace) -> None:
    root = _workspace_root(args)
    projects_dir = root / "projects"
    projects = []
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir()):
            if d.is_dir():
                projects.append(d.name)
    print(f"workspace_root={root}")
    print(f"projects_count={len(projects)}")
    if projects:
        for pid in projects[:200]:
            pdir = projects_dir / pid
            repo_ok = (pdir / "repo").exists()
            state_ok = (pdir / "state").exists()
            print(f"- {pid} repo={repo_ok} state={state_ok}")


def cmd_workspace_doctor(args: argparse.Namespace) -> None:
    root = _workspace_root(args)
    if not root.exists():
        print(f"workspace: FAIL missing_root={root}")
        print("next: openteam workspace init")
        raise SystemExit(2)

    # Governance: workspace must be OUTSIDE the openteam repo.
    repo_root = _find_openteam_repo_root()
    if repo_root and _is_within(root, repo_root):
        print(f"workspace: FAIL workspace_root_inside_repo root={root} repo={repo_root}")
        print("next: openteam workspace init --path ~/.openteam/workspace")
        raise SystemExit(2)

    must = [
        root / "projects",
        root / "shared" / "cache",
        root / "shared" / "tmp",
        root / "config",
    ]
    miss = [str(p) for p in must if not p.exists()]
    if miss:
        print("workspace: FAIL missing_paths=" + ",".join(miss[:5]))
        print("next: openteam workspace init")
        raise SystemExit(2)
    # Basic writability check.
    try:
        t = root / "shared" / "tmp" / f"doctor_{os.getpid()}.tmp"
        t.write_text("ok\n", encoding="utf-8")
        t.unlink(missing_ok=True)
    except Exception as e:
        print(f"workspace: FAIL not_writable err={e}")
        raise SystemExit(2)
    print(f"workspace_root={root}")

    # Per-project structure checks.
    bad_projects: list[str] = []
    missing_by_project: dict[str, list[str]] = {}
    projects_dir = root / "projects"
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir():
                continue
            pid = d.name
            if not _is_safe_project_id(pid):
                bad_projects.append(pid)
                continue
            req = d / "state" / "requirements" / "requirements.yaml"
            must_paths = [
                d / "repo",
                d / "state" / "ledger" / "tasks",
                d / "state" / "logs" / "tasks",
                d / "state" / "requirements" / "conflicts",
                d / "state" / "prompts" / "MASTER_PROMPT.md",
                d / "state" / "plan" / "plan.yaml",
                d / "state" / "plan" / "PLAN.md",
                d / "state" / "kb",
                d / "state" / "cluster",
                req,
            ]
            miss = [str(p.relative_to(d)) for p in must_paths if not p.exists()]
            if miss:
                missing_by_project[pid] = miss
    if bad_projects:
        print("workspace: FAIL invalid_project_ids=" + ",".join(bad_projects[:10]))
        print("next: rename project dirs to lowercase [a-z0-9][a-z0-9_-]{0,63}")
        raise SystemExit(2)
    if missing_by_project:
        first = sorted(missing_by_project.keys())[0]
        print(f"workspace: FAIL missing_project_paths project_id={first} missing={missing_by_project[first][:8]}")
        print("next: openteam workspace init  # idempotent repair")
        raise SystemExit(2)

    print("workspace: OK")


def cmd_workspace_migrate(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root (for --from-repo migration).")
    if not getattr(args, "from_repo", False):
        raise RuntimeError("Only supported mode: --from-repo")

    root = _workspace_root(args)
    # Local governance script (no remote writes).
    script = repo_root / "scripts" / "governance" / "migrate_repo_projects.py"
    if not script.exists():
        raise RuntimeError(f"migration script missing: {script}")

    apply = bool(getattr(args, "force", False))
    if apply:
        _approval_gate(
            args,
            repo_root=repo_root,
            action_kind="workspace_migrate_force",
            summary="workspace migrate --from-repo --force (move legacy project artifacts out of openteam repo)",
            payload={"from_repo": True, "workspace_root": str(root)},
            yes=bool(getattr(args, "yes", False)),
        )

    cmd = [
        sys.executable,
        str(script),
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(root),
    ]
    if getattr(args, "dry_run", False) or (not apply):
        cmd.append("--dry-run")
    if apply:
        cmd.append("--force")
    p = subprocess.run(cmd, check=False)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def cmd_project_list(args: argparse.Namespace) -> None:
    root = _workspace_root(args)
    projects_dir = root / "projects"
    if not projects_dir.exists():
        print(f"workspace_missing: {root}")
        print("next: openteam workspace init")
        raise SystemExit(2)
    rows: list[list[str]] = []
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        pid = d.name
        repo = d / "repo"
        state = d / "state"
        req = state / "requirements" / "requirements.yaml"
        tasks = state / "ledger" / "tasks"
        rows.append(
            [
                pid,
                "Y" if repo.exists() else "",
                "Y" if state.exists() else "",
                "Y" if req.exists() else "",
                str(len(list(tasks.glob("*.yaml")))) if tasks.exists() else "0",
            ]
        )
    print(_fmt_table(["project_id", "repo", "state", "requirements", "tasks"], rows))


def _require_project_id(pid: str) -> str:
    s = _norm(pid).lower()
    if not _is_safe_project_id(s):
        raise RuntimeError(f"invalid --project id: {pid!r} (allowed: [a-z0-9][a-z0-9_-]{{0,63}})")
    return s


def _project_repo_dir(workspace_root: Path, project_id: str) -> Path:
    return workspace_root / "projects" / project_id / "repo"


def _detect_workspace_project_from_cwd(workspace_root: Path, cwd: Optional[Path] = None) -> str:
    cur = (cwd or Path.cwd()).resolve()
    base = (workspace_root / "projects").resolve()
    try:
        rel = cur.relative_to(base)
    except Exception:
        return ""
    parts = rel.parts
    if len(parts) < 2:
        return ""
    pid = str(parts[0] or "").strip()
    seg = str(parts[1] or "").strip()
    if seg != "repo":
        return ""
    return _require_project_id(pid)


def _project_repl(args: argparse.Namespace, *, project_id: str) -> int:
    base, _prof = _base_url(args)
    print(f"project_repl: project_id={project_id} scope=project:{project_id}")
    print("输入会落盘为 Raw，不要输入密码/密钥。")
    print("Enter requirement text. Commands: /exit /help /status")
    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            break
        if not line:
            break
        text = line.rstrip("\n")
        if not text.strip():
            continue
        cmd = text.strip()
        if cmd in ("/exit", "/quit"):
            break
        if cmd == "/help":
            print("commands: /exit /help /status ; any other text is captured as RAW requirement")
            continue
        if cmd == "/status":
            st = _http_json("GET", base + "/v1/status")
            instance_id = str(st.get("instance_id") or "").strip()
            leader_base = ""
            if isinstance(st.get("leader"), dict):
                leader_base = str((st.get("leader") or {}).get("leader_base_url") or "").strip()
            print(f"status.instance_id={instance_id}")
            if leader_base:
                print(f"status.leader_base_url={leader_base}")
            continue
        out = _http_json(
            "POST",
            base + "/v1/requirements/add",
            {"scope": f"project:{project_id}", "text": text, "source": "cli", "workstream_id": "general"},
            timeout_sec=120,
        )
        print(str(out.get("summary") or "").rstrip())
    return 0


def _inject_project_agents_manual(args: argparse.Namespace, *, project_id: str, repo_path: Optional[str] = None, reason: str = "") -> None:
    """
    Best-effort: ensure project repo root AGENTS.md contains Team-OS manual block.
    Non-leader runs are plan-only (leader-only enforced by pipeline).
    """
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the openteam repo.")

    # Prefer passing profile/base_url for stable leader check.
    base_url = ""
    prof_name = ""
    try:
        base_url, prof = _base_url(args)
        prof_name = str(prof.get("name") or "").strip()
    except Exception:
        pass

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        str(project_id),
        "--manual-version",
        "v1",
    ]
    if repo_path:
        argv += ["--repo-path", str(repo_path)]
    if prof_name:
        argv += ["--profile", prof_name]
    if base_url:
        argv += ["--base-url", base_url]

    # Dry-run only if caller asked.
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")

    # Note: we don't try to commit/push project repo here; that's a project-level workflow.
    if reason:
        print(f"project_agents_inject.trigger={reason} project_id={project_id}")
    _run_pipeline(repo_root, "scripts/pipelines/project_agents_inject.py", argv)


def cmd_project_config_init(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the openteam repo.")

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        pid,
    ]
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    argv.append("init")
    _run_pipeline(repo_root, "scripts/pipelines/project_config.py", argv)

    # Hook: ensure project repo AGENTS.md contains the manual block.
    _inject_project_agents_manual(args, project_id=pid, reason="project_config_init")


def cmd_project_config_show(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the openteam repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        pid,
        "show",
    ]
    _run_pipeline(repo_root, "scripts/pipelines/project_config.py", argv)


def cmd_project_config_set(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the openteam repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        pid,
    ]
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    argv += ["set", "--key", str(args.key), "--value", str(args.value)]
    _run_pipeline(repo_root, "scripts/pipelines/project_config.py", argv)


def cmd_project_config_validate(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the openteam repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--project",
        pid,
        "validate",
    ]
    _run_pipeline(repo_root, "scripts/pipelines/project_config.py", argv)

    # Hook: if validate runs, ensure AGENTS manual exists (idempotent).
    _inject_project_agents_manual(args, project_id=pid, reason="project_config_validate")


def cmd_project_agents_inject(args: argparse.Namespace) -> None:
    pid = _require_project_id(args.project)
    repo_path = str(getattr(args, "repo_path", "") or "").strip() or str(_project_repo_dir(_workspace_root(args), pid))
    _inject_project_agents_manual(args, project_id=pid, repo_path=repo_path, reason="explicit")


#
# NOTE: Repo-improvement is runtime-managed via the control-plane.
# The CLI must NOT auto-trigger team workflows on every command.
#


def cmd_config_init(_args: argparse.Namespace) -> None:
    if CONFIG_PATH.exists():
        eprint(f"config_exists={CONFIG_PATH}")
        return
    cfg = {
        "current_profile": "local",
        "workspace_root": str(DEFAULT_WORKSPACE_ROOT),
        "default_project_id": "openteam",
        "leader_only_writes": True,
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8787",
                # Prefer the real OpenTeam dev project by default; demos are opt-in.
                "default_project_id": "openteam",
            }
        },
    }
    _save_config(cfg)
    print(f"config_created={CONFIG_PATH}")


def cmd_config_add_profile(args: argparse.Namespace) -> None:
    cfg = _load_config()
    profiles = cfg.get("profiles", {}) or {}
    profiles[args.name] = {"base_url": args.base_url, "default_project_id": args.default_project_id or ""}
    cfg["profiles"] = profiles
    if not cfg.get("current_profile"):
        cfg["current_profile"] = args.name
    _save_config(cfg)
    print(f"profile_added={args.name}")


def cmd_config_use(args: argparse.Namespace) -> None:
    cfg = _load_config()
    profiles = cfg.get("profiles", {}) or {}
    if args.name not in profiles:
        raise RuntimeError(f"Unknown profile: {args.name}")
    cfg["current_profile"] = args.name
    _save_config(cfg)
    print(f"profile_in_use={args.name}")


def cmd_config_show(_args: argparse.Namespace) -> None:
    cfg = _load_config()
    print(CONFIG_PATH)
    print(_dump_toml(cfg))


def _base_url(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    cfg = _load_config()
    prof = _get_profile(cfg, args.profile)
    base = str(prof.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("profile.base_url is empty; run: openteam config show")
    return base, prof


def _runtime_root_for_repo(repo_root: Path) -> Path:
    raw = str(os.getenv("OPENTEAM_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    home = str(os.getenv("OPENTEAM_HOME") or "").strip()
    if home:
        return (Path(home).expanduser().resolve() / "runtime" / "default").resolve()
    return (Path.home() / ".openteam" / "runtime" / "default").resolve()


def _team_status_doc(*, base_url: str = "") -> dict[str, Any]:
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return {}
    try:
        return _http_json("GET", url + "/v1/status")
    except Exception:
        return {}


def _default_team_id_from_status(status: dict[str, Any]) -> str:
    team_id = _norm(status.get("default_team_id"))
    if team_id:
        return team_id
    teams = status.get("teams") if isinstance(status, dict) else {}
    if isinstance(teams, dict) and teams:
        return sorted(str(key) for key in teams.keys() if str(key).strip())[0]
    return ""


def _team_summary_from_status(status: dict[str, Any], *, team_id: str) -> dict[str, Any]:
    teams = status.get("teams") if isinstance(status, dict) else {}
    if not isinstance(teams, dict):
        return {}
    team = teams.get(team_id)
    return dict(team) if isinstance(team, dict) else {}


def _read_last_team_run(repo_root: Path, *, base_url: str = "", team_id: str = "") -> dict[str, Any]:
    _ = repo_root
    status = _team_status_doc(base_url=base_url)
    wanted_team_id = str(team_id or "").strip() or _default_team_id_from_status(status)
    if not wanted_team_id:
        return {}
    team = _team_summary_from_status(status, team_id=wanted_team_id)
    last = team.get("last_run") if isinstance(team, dict) else {}
    return last if isinstance(last, dict) else {}


def _default_project_id(prof: dict[str, Any], args: argparse.Namespace) -> str:
    return getattr(args, "project", "") or (prof.get("default_project_id") or "openteam")


def _default_scope(prof: dict[str, Any], args: argparse.Namespace) -> str:
    s = _norm(getattr(args, "scope", "") or "")
    if s:
        return s
    pid = _default_project_id(prof, args)
    return "openteam" if pid == "openteam" else f"project:{pid}"


def cmd_status(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    st = _team_status_doc(base_url=base)
    instance_id = st.get("instance_id", "")
    focus = st.get("current_focus") or {}
    default_team_id = _default_team_id_from_status(st)
    default_team = _team_summary_from_status(st, team_id=default_team_id) if default_team_id else {}

    project_id = _default_project_id(prof, args)
    workstream_id = args.workstream

    pending = st.get("pending_decisions") or []
    if pending and (not getattr(args, "all_decisions", False)):
        # Default: show pending decisions relevant to the selected project.
        tasks_all = st.get("tasks") or []
        task_to_project: dict[str, str] = {}
        for t in tasks_all:
            tid = _norm(t.get("task_id"))
            pid = _norm(t.get("project_id"))
            if tid and pid:
                task_to_project[tid] = pid

        filtered: list[dict[str, Any]] = []
        for d in pending:
            pid = _norm(d.get("project_id"))
            if pid:
                if pid == project_id:
                    filtered.append(d)
                continue
            tid = _norm(d.get("task_id"))
            if tid and task_to_project.get(tid) == project_id:
                filtered.append(d)
        pending = filtered
    if pending:
        print(f"PENDING_DECISIONS ({len(pending)}) profile={prof['name']} instance_id={instance_id}")
        for i, d in enumerate(pending, 1):
            dtype = str(d.get("type") or "").strip()
            pid = str(d.get("project_id") or "").strip()
            rid = str(d.get("req_id") or "").strip()
            tid = str(d.get("task_id") or "").strip()
            key = rid or tid
            print(f"{i}. {dtype} {pid} {key}".strip())
        print()

    print(f"profile={prof['name']} instance_id={instance_id}")
    if st.get("workspace_root") is not None:
        print(f"workspace_root={st.get('workspace_root','')}")
    if st.get("workspace_projects_count") is not None:
        print(f"workspace_projects_count={st.get('workspace_projects_count','')}")
    print(f"focus.objective={focus.get('objective','')}")
    print(f"focus.updated_at={focus.get('updated_at','')}")
    repo_root = _find_openteam_repo_root()
    if repo_root:
        last = _read_last_team_run(repo_root, base_url=base, team_id=default_team_id) or {}
        if last.get("ts"):
            print(f"team.last_run_at={last.get('ts')}")
            print(f"team.status={last.get('status','')}")
            if last.get("records") is not None:
                print(f"team.records={last.get('records')}")
    if default_team_id:
        print(f"default_team_id={default_team_id}")
    proposal_counts = default_team.get("proposal_counts") or {}
    if proposal_counts:
        print(f"team.pending_proposals={proposal_counts.get('pending', 0)}")
        print(f"team.proposals_total={proposal_counts.get('total', 0)}")
    coding = default_team.get("coding") or {}
    delivery_summary = coding.get("summary") or {}
    if delivery_summary:
        print(f"team.coding_total={delivery_summary.get('total', 0)}")
        print(f"team.coding_queued={delivery_summary.get('queued', 0)}")
        print(f"team.coding_active={delivery_summary.get('coding', 0)}")
        print(f"team.coding_blocked={delivery_summary.get('blocked', 0)}")
    print()

    agents = st.get("agents") or []
    if project_id:
        agents = [a for a in agents if a.get("project_id") == project_id]
    if workstream_id:
        agents = [a for a in agents if a.get("workstream_id") == workstream_id]

    active_agents = [a for a in agents if _agent_is_active(a.get("state"))]
    active_tasks_by_id: dict[str, list[dict[str, Any]]] = {}
    for a in active_agents:
        tid = _norm(a.get("task_id"))
        if tid:
            active_tasks_by_id.setdefault(tid, []).append(a)

    print(
        f"active_agents={len(active_agents)} active_tasks={len(active_tasks_by_id)} "
        f"(project_id={project_id}{' workstream_id='+workstream_id if workstream_id else ''})"
    )
    if active_tasks_by_id:
        rows = []
        for tid in sorted(active_tasks_by_id.keys()):
            group = active_tasks_by_id[tid]
            roles = ",".join(sorted({str(x.get('role_id', '')) for x in group if str(x.get('role_id', '')).strip()}))
            last_hb = max([_norm(x.get("last_heartbeat")) for x in group], default="")
            rows.append([tid, str(len(group)), roles[:40], last_hb])
        print(_fmt_table(["task", "agents", "roles", "last_heartbeat"], rows))
    else:
        print("(no active tasks)")
    print()

    rows = []
    for a in agents:
        rows.append(
            [
                str(a.get("agent_id", ""))[:8],
                str(a.get("role_id", "")),
                str(a.get("state", "")),
                str(a.get("task_id", "")),
                str(a.get("current_action", ""))[:40],
                str(a.get("last_heartbeat", "")),
            ]
        )
    print(
        f"agents_total={len(rows)} active={len(active_agents)} "
        f"(project_id={project_id}{' workstream_id='+workstream_id if workstream_id else ''})"
    )
    if rows:
        print(_fmt_table(["agent", "role", "state", "task", "action", "heartbeat"], rows))
    else:
        print("(none)")
    print()

    tasks = st.get("tasks") or []
    if project_id:
        tasks = [t for t in tasks if t.get("project_id") == project_id]
    if workstream_id:
        tasks = [t for t in tasks if t.get("workstream_id") == workstream_id]
    rows = []
    for t in tasks:
        tid = _norm(t.get("task_id"))
        agents_n = len(active_tasks_by_id.get(tid, [])) if tid else 0
        rows.append(
            [
                tid,
                _display_task_state(t.get("state", "")),
                str(t.get("owner_role", "")),
                str(t.get("workstream_id", "")),
                "YES" if t.get("need_pm_decision") else "",
                str(agents_n),
            ]
        )
    print(f"tasks={len(rows)}")
    if rows:
        print(_fmt_table(["task_id", "state", "owner", "workstream", "NEED_PM", "agents"], rows))
    else:
        print("(none)")


def cmd_focus(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    if args.set:
        payload = {"objective": args.set}
        out = _http_json("POST", base + "/v1/focus", payload)
        print(f"updated objective={out.get('objective','')}")
        return
    out = _http_json("GET", base + "/v1/focus")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_agents(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args) if not args.all else None
    q = {}
    if project_id:
        q["project_id"] = project_id
    if args.workstream:
        q["workstream_id"] = args.workstream
    if args.state:
        q["state"] = args.state
    if args.role:
        q["role_id"] = args.role
    url = base + "/v1/agents"
    if q:
        url += "?" + urllib.parse.urlencode(q)
    out = _http_json("GET", url)
    agents = out.get("agents") or []
    rows = []
    for a in agents:
        rows.append(
            [
                str(a.get("agent_id", ""))[:8],
                str(a.get("role_id", "")),
                str(a.get("project_id", "")),
                str(a.get("workstream_id", "")),
                str(a.get("task_id", "")),
                str(a.get("state", "")),
                str(a.get("current_action", ""))[:50],
                str(a.get("last_heartbeat", "")),
            ]
        )
    if rows:
        print(_fmt_table(["agent", "role", "project", "workstream", "task", "state", "action", "heartbeat"], rows))
    else:
        print("(none)")


def cmd_tasks(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args) if args.project or not args.all else None
    q = {"limit": args.limit, "offset": args.offset}
    if project_id:
        q["project_id"] = project_id
    if args.workstream:
        q["workstream_id"] = args.workstream
    if args.state:
        # Backward compatible aliases: running/work -> doing
        st = str(args.state or "").strip().lower()
        if st in ("running", "work", "in_progress", "inprogress"):
            st = "doing"
        q["state"] = st
    url = base + "/v1/tasks?" + urllib.parse.urlencode(q)
    out = _http_json("GET", url)
    tasks = out.get("tasks") or []
    rows = []
    for t in tasks:
        rows.append(
            [
                str(t.get("task_id", "")),
                _display_task_state(t.get("state", "")),
                str(t.get("owner_role", "")),
                str(t.get("project_id", "")),
                str(t.get("workstream_id", "")),
                "YES" if t.get("need_pm_decision") else "",
                str(t.get("risk", "")),
            ]
        )
    if rows:
        print(_fmt_table(["task_id", "state", "owner", "project", "workstream", "NEED_PM", "risk"], rows))
    else:
        print("(none)")


def cmd_req_add(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    # Project-scope guardrails: ensure Workspace scaffold exists and AGENTS manual is injected.
    pid_for_scope = ""
    if str(scope or "").strip().startswith("project:"):
        pid_for_scope = _require_project_id(str(scope).split(":", 1)[1])
        _ensure_project_scaffold(_workspace_root(args), pid_for_scope)
    payload = {
        "scope": scope,
        "workstream_id": args.workstream,
        "text": args.text,
        "priority": args.priority,
        "rationale": args.rationale or "",
        "constraints": args.constraints or None,
        "acceptance": args.acceptance or None,
        "source": args.source or "cli",
    }
    out = _http_json("POST", base + "/v1/requirements/add", payload, timeout_sec=120)
    print(out.get("summary", "").rstrip())
    if out.get("pending_decisions"):
        print("\nPENDING_DECISIONS:")
        for d in out["pending_decisions"]:
            print(json.dumps(d, ensure_ascii=False))
    if pid_for_scope:
        _inject_project_agents_manual(args, project_id=pid_for_scope, reason="requirements_add")


def cmd_req_list(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    out = _http_json("GET", base + "/v1/requirements/show?scope=" + urllib.parse.quote(scope))
    reqs = out.get("requirements") or []
    rows = []
    for r in reqs:
        st = str(r.get("status", ""))
        if args.show_conflicts:
            rows.append(
                [
                    str(r.get("req_id", "")),
                    st,
                    str(r.get("priority", "")),
                    ",".join(r.get("conflicts_with") or []),
                    (",".join(r.get("decision_log_refs") or [])[:80]),
                    str(r.get("title", ""))[:60],
                ]
            )
        else:
            rows.append([str(r.get("req_id", "")), st, str(r.get("priority", "")), str(r.get("title", ""))[:60]])
    if rows:
        if args.show_conflicts:
            print(_fmt_table(["req_id", "status", "prio", "conflicts_with", "refs", "title"], rows))
        else:
            print(_fmt_table(["req_id", "status", "prio", "title"], rows))
    else:
        print("(none)")


def cmd_req_conflicts(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    out = _http_json("GET", base + "/v1/requirements/show?scope=" + urllib.parse.quote(scope))
    reqs = out.get("requirements") or []
    rows = []
    for r in reqs:
        st = str(r.get("status", "")).upper()
        if st in ("CONFLICT", "NEED_PM_DECISION"):
            rows.append(
                [
                    str(r.get("req_id", "")),
                    st,
                    ",".join(r.get("conflicts_with") or []),
                    (",".join(r.get("decision_log_refs") or [])[:80]),
                    str(r.get("title", ""))[:50],
                ]
            )
    if rows:
        print(_fmt_table(["req_id", "status", "conflicts_with", "refs", "title"], rows))
    else:
        print("(none)")


def cmd_req_import(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    pid_for_scope = ""
    if str(scope or "").strip().startswith("project:"):
        pid_for_scope = _require_project_id(str(scope).split(":", 1)[1])
        _ensure_project_scaffold(_workspace_root(args), pid_for_scope)
    p = Path(args.file).expanduser()
    if not p.exists():
        raise RuntimeError(f"file not found: {p}")
    content = p.read_text(encoding="utf-8")
    payload = {"scope": scope, "filename": p.name, "content_text": content, "workstream_id": args.workstream, "source": "import"}
    out = _http_json("POST", base + "/v1/requirements/import", payload, timeout_sec=120)
    print(out.get("summary", "").rstrip())
    if out.get("pending_decisions"):
        print("\nPENDING_DECISIONS:")
        for d in out["pending_decisions"]:
            print(json.dumps(d, ensure_ascii=False))
    if pid_for_scope:
        _inject_project_agents_manual(args, project_id=pid_for_scope, reason="requirements_import")


def cmd_req_verify(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    out = _http_json("POST", base + "/v1/requirements/verify", {"scope": scope}, timeout_sec=60)
    ok = bool(out.get("ok"))
    print(f"ok={ok} scope={scope}")
    drift = out.get("drift") or {}
    if not drift.get("ok"):
        print("drift: FAIL")
        for p in drift.get("points") or []:
            print(f"- {p}")
    conflicts = out.get("conflicts") or []
    if conflicts:
        print(f"conflicts: {len(conflicts)}")
        for c in conflicts[:50]:
            print(json.dumps(c, ensure_ascii=False))


def cmd_req_rebuild(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    pid_for_scope = ""
    if str(scope or "").strip().startswith("project:"):
        pid_for_scope = _require_project_id(str(scope).split(":", 1)[1])
        _ensure_project_scaffold(_workspace_root(args), pid_for_scope)
    out = _http_json("POST", base + "/v1/requirements/rebuild", {"scope": scope}, timeout_sec=60)
    print(json.dumps(out, ensure_ascii=False, indent=2).rstrip())
    if pid_for_scope:
        _inject_project_agents_manual(args, project_id=pid_for_scope, reason="requirements_rebuild")


def cmd_req_baseline_show(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    url = base + "/v1/requirements/baseline/show?scope=" + urllib.parse.quote(scope) + "&max_chars=" + urllib.parse.quote(str(args.max_chars))
    out = _http_json("GET", url, timeout_sec=60)
    print(f"scope={scope}")
    items = out.get("baselines") or []
    if not items:
        print("(none)")
        return
    for it in items[:50]:
        name = _norm(it.get("name"))
        path = _norm(it.get("path"))
        print(f"\n== {name} ==")
        if path:
            print(f"path={path}")
        prev = _norm(it.get("text_preview"))
        if prev:
            print(prev.rstrip())


def cmd_req_baseline_set_v2(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    scope = _default_scope(prof, args)
    payload = {"scope": scope, "text": args.text, "reason": args.reason}
    out = _http_json("POST", base + "/v1/requirements/baseline/set-v2", payload, timeout_sec=120)
    print(json.dumps(out, ensure_ascii=False, indent=2).rstrip())


def cmd_chat(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)
    workstream_id = args.workstream

    prompt = "Type a message and press Enter. Commands: /req <text>, /pause, /resume, /stop, /quit"
    print(prompt)
    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            break
        if not line:
            break
        line = line.rstrip("\n")
        if not line.strip():
            continue
        if line.strip() in ("/quit", "/exit"):
            break

        msg_type = "GENERAL"
        msg = line
        if line.startswith("/req "):
            msg_type = "NEW_REQUIREMENT"
            msg = line[len("/req ") :].strip()
        elif line.strip() == "/pause":
            msg_type = "PAUSE"
            msg = "pause"
        elif line.strip() == "/resume":
            msg_type = "RESUME"
            msg = "resume"
        elif line.strip() == "/stop":
            msg_type = "STOP"
            msg = "stop"

        payload = {
            "project_id": project_id,
            "workstream_id": workstream_id,
            "run_id": args.run,
            "message": msg,
            "message_type": msg_type,
        }
        out = _http_json("POST", base + "/v1/chat", payload, timeout_sec=120)
        print(out.get("response_text", "").rstrip())


def cmd_doctor(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    base, prof = _base_url(args)
    _run_pipeline(
        repo_root,
        "scripts/pipelines/doctor.py",
        [
            "--repo-root",
            str(repo_root),
            "--workspace-root",
            str(_workspace_root(args)),
            "--profile",
            str(prof.get("name") or ""),
            "--base-url",
            base,
        ],
    )


def cmd_policy_check(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")

    script = repo_root / "scripts" / "policy_check.py"
    if not script.exists():
        raise RuntimeError(f"policy_check script missing: {script}")

    cmd = [sys.executable, str(script), "--repo-root", str(repo_root)]
    if getattr(args, "json", False):
        cmd.append("--json")
    if getattr(args, "quiet", False):
        cmd.append("--quiet")
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def cmd_db_migrate(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))]
    if getattr(args, "db_url", ""):
        argv += ["--db-url", str(args.db_url).strip()]
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/db_migrate.py", argv)


def cmd_approvals_list(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--json",
        "list",
        "--limit",
        str(int(getattr(args, "limit", 50) or 50)),
    ]
    _run_pipeline(repo_root, "scripts/pipelines/approvals.py", argv)


def cmd_hub_init(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))]
    argv += ["--pg-port", str(int(getattr(args, "pg_port", 5432) or 5432))]
    argv += ["--redis-port", str(int(getattr(args, "redis_port", 6379) or 6379))]
    _run_pipeline(repo_root, "scripts/pipelines/hub_init.py", argv)


def cmd_hub_up(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _run_pipeline(repo_root, "scripts/pipelines/hub_up.py", ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))])


def cmd_hub_down(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _run_pipeline(repo_root, "scripts/pipelines/hub_down.py", ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))])


def cmd_hub_status(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _run_pipeline(repo_root, "scripts/pipelines/hub_status.py", ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))])


def cmd_hub_logs(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--tail", str(int(getattr(args, "tail", 200) or 200))]
    if str(getattr(args, "service", "") or "").strip():
        argv += ["--service", str(args.service).strip()]
    _run_pipeline(repo_root, "scripts/pipelines/hub_logs.py", argv)


def cmd_hub_migrate(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _run_pipeline(repo_root, "scripts/pipelines/hub_migrate.py", ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))])


def cmd_hub_expose(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _approval_gate(
        args,
        repo_root=repo_root,
        action_kind="hub_expose_remote_access",
        summary=f"hub expose bind_ip={args.bind_ip} allow_cidrs={args.allow_cidrs} open_redis={bool(args.open_redis)}",
        payload={
            "bind_ip": str(args.bind_ip),
            "allow_cidrs": str(args.allow_cidrs),
            "open_redis": bool(args.open_redis),
        },
    )
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--bind-ip",
        str(args.bind_ip),
        "--allow-cidrs",
        str(args.allow_cidrs),
    ]
    if bool(args.open_redis):
        argv.append("--open-redis")
    _run_pipeline(repo_root, "scripts/pipelines/hub_expose.py", argv)


def cmd_hub_backup(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args))]
    if str(getattr(args, "output", "") or "").strip():
        argv += ["--output", str(args.output).strip()]
    _run_pipeline(repo_root, "scripts/pipelines/hub_backup.py", argv)


def cmd_hub_restore(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _approval_gate(
        args,
        repo_root=repo_root,
        action_kind="hub_restore",
        summary=f"hub restore file={args.file}",
        payload={"file": str(args.file)},
    )
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--file", str(args.file)]
    _run_pipeline(repo_root, "scripts/pipelines/hub_restore.py", argv)


def cmd_hub_export_config(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--format", str(args.format)]
    _run_pipeline(repo_root, "scripts/pipelines/hub_export_config.py", argv)


def cmd_hub_push_config(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _approval_gate(
        args,
        repo_root=repo_root,
        action_kind="hub_push_config_with_secrets",
        summary=f"hub push-config host={args.host} user={args.user}",
        payload={"host": str(args.host), "user": str(args.user), "password_stdin": bool(args.password_stdin), "ssh_key": str(args.ssh_key or "")},
    )
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--host",
        str(args.host),
        "--user",
        str(args.user),
        "--remote-env-path",
        str(args.remote_env_path),
    ]
    if str(getattr(args, "ssh_key", "") or "").strip():
        argv += ["--ssh-key", str(args.ssh_key).strip()]
    if str(getattr(args, "hub_host", "") or "").strip():
        argv += ["--hub-host", str(args.hub_host).strip()]
    env = None
    if bool(getattr(args, "password_stdin", False)):
        pw = sys.stdin.read().strip()
        if not pw:
            raise RuntimeError("--password-stdin was provided but stdin was empty")
        argv.append("--password-stdin")
        env = dict(os.environ)
        env["OPENTEAM_SSH_PASSWORD"] = pw
    _run_pipeline(repo_root, "scripts/pipelines/hub_push_config.py", argv, env=env)


def cmd_panel_show(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)

    cfg = _http_json("GET", base + "/v1/panel/github/config", timeout_sec=10)
    health = _http_json("GET", base + "/v1/panel/github/health?project_id=" + urllib.parse.quote(project_id), timeout_sec=10)

    print(f"profile={prof['name']} project_id={project_id}")
    print(f"mapping_path={cfg.get('mapping_path','')}")
    print(f"mapping_sha256={cfg.get('mapping_sha256','')}")
    print()

    proj_cfg = None
    for p in (cfg.get("projects") or []):
        if str(p.get("project_id")) == project_id:
            proj_cfg = p
            break
    if not proj_cfg:
        print("panel: NOT_CONFIGURED (no mapping entry for this project_id)")
    else:
        print("panel: github_projects")
        print(f"  owner_type={proj_cfg.get('owner_type','')}")
        print(f"  owner={proj_cfg.get('owner','')}")
        if proj_cfg.get("repo"):
            print(f"  repo={proj_cfg.get('repo','')}")
        print(f"  project_number={proj_cfg.get('project_number','')}")
        if proj_cfg.get("project_url"):
            print(f"  url={proj_cfg.get('project_url')}")
        print()

    last = (health.get("last_sync") or {}) if isinstance(health.get("last_sync"), dict) else {}
    summ = (health.get("summary") or {}) if isinstance(health.get("summary"), dict) else {}
    auto = (health.get("auto_sync") or {}) if isinstance(health.get("auto_sync"), dict) else {}
    if summ:
        print(f"sync_runs_total={summ.get('runs_total')} failures_total={summ.get('failures_total')}")
    if auto:
        print(f"auto_sync.enabled={auto.get('enabled')} interval_sec={auto.get('interval_sec')} debounce_sec={auto.get('debounce_sec')}")
    if "writes_enabled" in health:
        print(f"writes_enabled={health.get('writes_enabled')}")
    if "needs_full_resync" in health:
        print(f"needs_full_resync={health.get('needs_full_resync')}")

    if last:
        print("last_sync:")
        print(f"  ts_end={last.get('ts_end','')}")
        print(f"  ok={last.get('ok')}")
        print(f"  mode={last.get('mode','')}")
        print(f"  dry_run={last.get('dry_run')}")
        print(f"  stats={json.dumps(last.get('stats') or {}, ensure_ascii=False)}")
        if last.get("error"):
            print(f"  error={str(last.get('error'))[:200]}")
    else:
        print("last_sync: (none)")


def cmd_panel_health(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)
    out = _http_json("GET", base + "/v1/panel/github/health?project_id=" + urllib.parse.quote(project_id), timeout_sec=10)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_panel_sync(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)
    payload = {
        "project_id": project_id,
        "mode": "full" if args.full else "incremental",
        "dry_run": bool(args.dry_run),
    }
    out = _http_json("POST", base + "/v1/panel/github/sync", payload, timeout_sec=300)
    stats = out.get("stats") or {}
    print(f"project_id={out.get('project_id','')} mode={out.get('mode','')} dry_run={out.get('dry_run')}")
    if out.get("project_url"):
        print(f"project_url={out.get('project_url')}")
    print(f"stats={json.dumps(stats, ensure_ascii=False)}")
    if out.get("errors"):
        print("errors:")
        for e in out["errors"][:20]:
            print(f"- {str(e)[:300]}")
    if out.get("actions"):
        print("actions:")
        for a in out["actions"][:50]:
            print(f"- {a.get('action')} {a.get('kind')} {a.get('key')} {a.get('status')}".rstrip())


def cmd_panel_open(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)
    cfg = _http_json("GET", base + "/v1/panel/github/config", timeout_sec=10)
    proj_cfg = None
    for p in (cfg.get("projects") or []):
        if str(p.get("project_id")) == project_id:
            proj_cfg = p
            break
    url = (proj_cfg or {}).get("project_url") or ""
    if not url:
        print("panel_url: (missing) - set projects.<project_id>.project_url in mapping.yaml")
    else:
        print(url)


def cmd_team_list(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    out = _http_json("GET", base + "/v1/teams")
    teams = list(out.get("teams") or [])
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"teams_total={len(teams)}")
    if not teams:
        print("(none)")
        return
    rows: list[list[str]] = []
    for team in teams:
        rows.append(
            [
                str(team.get("team_id") or ""),
                str(team.get("display_name_zh") or "")[:20],
                ",".join([str(x) for x in (team.get("workflow_ids") or [])]),
                str(team.get("mission") or "")[:80],
            ]
        )
    print(_fmt_table(["team_id", "display_name_zh", "workflow_ids", "mission"], rows))


def cmd_team_run(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    team_id = str(getattr(args, "team_id", "") or "").strip()
    if not team_id:
        raise RuntimeError("team_id is required")
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    explicit_repo = str(getattr(args, "repo_path", "") or "").strip()
    repo_url = str(getattr(args, "repo_url", "") or "").strip()
    target_id = str(getattr(args, "target_id", "") or "").strip()
    target_repo = Path(explicit_repo).expanduser().resolve() if explicit_repo else repo_root
    include_repo_path = bool(explicit_repo) or (not target_id and not repo_url)
    payload = {
        "project_id": _default_project_id(prof, args),
        "workstream_id": str(getattr(args, "workstream", "") or "general").strip() or "general",
        "objective": str(getattr(args, "objective", "") or f"CLI-triggered team:{team_id}").strip(),
        "target_id": target_id or None,
        "repo_path": str(target_repo) if include_repo_path else "",
        "repo_url": repo_url or None,
        "repo_locator": str(getattr(args, "repo_locator", "") or "").strip(),
        "dry_run": bool(args.dry_run),
        "force": bool(args.force),
        "trigger": "cli",
    }
    out = _http_json("POST", base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/run", payload)
    if not bool(out.get("ok")):
        raise RuntimeError(str(out.get("error") or "team_run_failed"))
    if bool(getattr(args, "quiet", False)):
        return
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _resolve_team_watch_run_id(base: str, *, team_id: str, project_id: str, explicit_run_id: str) -> str:
    rid = str(explicit_run_id or "").strip()
    if rid:
        return rid
    query = ""
    if str(project_id or "").strip():
        query = "?project_id=" + urllib.parse.quote(str(project_id or "").strip(), safe="")
    out = _http_json("GET", base + "/v1/runs" + query)
    runs = list(out.get("runs") or [])
    marker = f"team:{str(team_id or '').strip().lower()}"
    for item in runs:
        state = str(item.get("state") or "").strip().upper()
        objective = str(item.get("objective") or "").strip().lower()
        if state == "RUNNING" and marker in objective:
            rid = str(item.get("run_id") or "").strip()
            if rid:
                return rid
    return ""


def cmd_team_watch(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    team_id = str(getattr(args, "team_id", "") or "").strip()
    if not team_id:
        raise RuntimeError("team_id is required")
    project_id = str(getattr(args, "project_id", "") or _default_project_id(prof, args)).strip()
    run_id = _resolve_team_watch_run_id(
        base,
        team_id=team_id,
        project_id=project_id,
        explicit_run_id=str(getattr(args, "run_id", "") or "").strip(),
    )
    if not run_id:
        raise RuntimeError("No active team run found. Pass --run-id or start one first.")
    url = base + f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/stream"
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=int(getattr(args, "timeout", 3600) or 3600)) as resp:
        for item in _iter_sse_events(resp):
            event = str(item.get("event") or "")
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            if bool(getattr(args, "json", False)):
                print(json.dumps({"event": event, "data": data}, ensure_ascii=False))
                continue
            if event == "run":
                run = data.get("run") if isinstance(data.get("run"), dict) else {}
                print(
                    f"[run] run_id={run.get('run_id','')} state={run.get('state','')} "
                    f"project_id={run.get('project_id','')} objective={run.get('objective','')}"
                )
                continue
            if event == "agent":
                print(
                    f"[agent] {data.get('role_id','')} state={data.get('state','')} "
                    f"task={data.get('task_id','')} action={data.get('current_action','')}"
                )
                continue
            if event == "runtime_event":
                print(_format_team_watch_event(data))
                continue
            if event == "end":
                run = data.get("run") if isinstance(data.get("run"), dict) else {}
                state = str(run.get("state") or data.get("state") or "").strip()
                print(f"[end] run_id={run.get('run_id', run_id)} state={state or 'DONE'}")
                return


def cmd_team_proposals(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    query: dict[str, str] = {}
    target_id = _norm(getattr(args, "target_id", "") or "")
    project_id = _norm(getattr(args, "project_id", "") or "")
    lane = _norm(getattr(args, "lane", "") or "")
    status = _norm(getattr(args, "status", "") or "")
    if target_id:
        query["target_id"] = target_id
    if project_id:
        query["project_id"] = project_id
    if lane:
        query["lane"] = lane
    if status:
        query["status"] = status
    url = base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/proposals"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    out = _http_json("GET", url)
    proposals = list(out.get("proposals") or [])
    if bool(getattr(args, "json", False)):
        print(json.dumps({"total": len(proposals), "proposals": proposals}, ensure_ascii=False, indent=2))
        return
    print(f"proposals_total={len(proposals)}")
    if not proposals:
        print("(none)")
        return
    rows: list[list[str]] = []
    for p in proposals:
        rows.append(
            [
                str(p.get("proposal_id") or ""),
                str(p.get("lane") or ""),
                str(p.get("status") or ""),
                str(p.get("version_bump") or ""),
                str(p.get("target_version") or ""),
                str(p.get("cooldown_until") or ""),
                str(p.get("discussion_issue_url") or "")[:48],
                str(p.get("title") or "")[:60],
            ]
        )
    print(_fmt_table(["proposal_id", "lane", "status", "bump", "target", "cooldown_until", "discussion_issue", "title"], rows))


def cmd_team_decide(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    payload = {
        "proposal_id": str(args.proposal_id),
        "action": str(args.action),
        "title": str(getattr(args, "title", "") or "").strip() or None,
        "summary": str(getattr(args, "summary", "") or "").strip() or None,
        "version_bump": str(getattr(args, "version_bump", "") or "").strip() or None,
    }
    out = _http_json("POST", base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/proposals/decide", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    proposal = out.get("proposal") or {}
    print(f"proposal_id={proposal.get('proposal_id','')}")
    print(f"status={proposal.get('status','')}")
    print(f"lane={proposal.get('lane','')}")
    print(f"title={proposal.get('title','')}")
    print(f"version_bump={proposal.get('version_bump','')}")
    print(f"target_version={proposal.get('target_version','')}")
    print(f"discussion_issue_url={proposal.get('discussion_issue_url','')}")


def cmd_team_discussions_sync(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    out = _http_json("POST", base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/discussions/sync", {})
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"ok={bool(out.get('ok'))}")
    print(f"scanned={out.get('scanned', 0)}")
    print(f"updated={out.get('updated', 0)}")
    print(f"replied={out.get('replied', 0)}")
    print(f"errors={out.get('errors', 0)}")


def cmd_team_coding_run(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    payload = {
        "project_id": _default_project_id(prof, args),
        "target_id": str(getattr(args, "target_id", "") or "").strip() or None,
        "task_id": str(getattr(args, "task_id", "") or "").strip() or None,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "force": bool(getattr(args, "force", False)),
        "concurrency": int(getattr(args, "concurrency", 10) or 10),
    }
    out = _http_json("POST", base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/coding/run", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"ok={bool(out.get('ok'))}")
    print(f"scanned={out.get('scanned', 0)}")
    print(f"processed={out.get('processed', 0)}")
    summary = out.get("summary") or {}
    if summary:
        print(f"coding.total={summary.get('total', 0)}")
        print(f"coding.queued={summary.get('queued', 0)}")
        print(f"coding.active={summary.get('coding', 0)}")
        print(f"coding.blocked={summary.get('blocked', 0)}")


def cmd_team_coding_tasks(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    query: dict[str, str] = {}
    project_id = _default_project_id(prof, args)
    target_id = _norm(getattr(args, "target_id", "") or "")
    status = _norm(getattr(args, "status", "") or "")
    if project_id:
        query["project_id"] = project_id
    if target_id:
        query["target_id"] = target_id
    if status:
        query["status"] = status
    url = base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/coding/tasks"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    out = _http_json("GET", url)
    tasks = list(out.get("tasks") or [])
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    summary = out.get("summary") or {}
    print(f"coding_tasks_total={len(tasks)}")
    if summary:
        print(f"coding.queued={summary.get('queued', 0)} coding.active={summary.get('coding', 0)} coding.blocked={summary.get('blocked', 0)}")
    if not tasks:
        print("(none)")
        return
    rows: list[list[str]] = []
    for task in tasks:
        rows.append(
            [
                str(task.get("task_id") or ""),
                str(task.get("status") or ""),
                str(task.get("stage") or ""),
                str(task.get("owner_role") or ""),
                str(task.get("attempt_count") or ""),
                str(task.get("pull_request_url") or "")[:48],
                str(task.get("title") or "")[:60],
            ]
        )
    print(_fmt_table(["task_id", "status", "stage", "owner_role", "attempts", "pull_request", "title"], rows))


def cmd_team_logs(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    run_id = str(getattr(args, "run_id", "") or "").strip()
    if not run_id:
        status = _team_status_doc(base_url=base)
        team = _team_summary_from_status(status, team_id=team_id)
        last_run = team.get("last_run") if isinstance(team, dict) else {}
        if isinstance(last_run, dict):
            run_id = str(last_run.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeError("No team run found. Start one first or pass --run-id.")
    limit = max(1, int(getattr(args, "limit", 200) or 200))
    url = base + f"/v1/teams/{urllib.parse.quote(team_id, safe='')}/runs/{urllib.parse.quote(run_id, safe='')}/logs?limit={limit}"
    out = _http_json("GET", url)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    run = out.get("run") or {}
    saved_logs = out.get("saved_logs") if isinstance(out.get("saved_logs"), dict) else {}
    print("Team Run")
    print("========")
    print(f"team_id: {team_id}")
    print(f"run_id: {run.get('run_id','')}")
    print(f"state: {run.get('state','')}")
    print(f"project_id: {run.get('project_id','')}")
    print(f"workstream_id: {run.get('workstream_id','')}")
    print(f"objective: {run.get('objective','')}")
    print(f"report_available: {bool(out.get('report_available'))}")
    print(f"planning_agent_logs: {len(list(out.get('planning_agent_logs') or []))}")
    print(f"events: {len(list(out.get('events') or []))}")
    if out.get("summary"):
        print(f"summary: {out.get('summary')}")
    if saved_logs:
        print()
        print("Saved Logs")
        print("----------")
        if saved_logs.get("markdown_path"):
            print(f"markdown: {saved_logs.get('markdown_path')}")
        if saved_logs.get("json_path"):
            print(f"json: {saved_logs.get('json_path')}")
    print()
    print("Planning Agent Logs")
    print("-------------------")
    planning_logs = list(out.get("planning_agent_logs") or [])
    if not planning_logs:
        print("(none)")
    for idx, item in enumerate(planning_logs, start=1):
        task_name = str(item.get("task_name") or "").strip() or "task"
        agent = str(item.get("agent") or "").strip() or "agent"
        raw = str(item.get("raw") or "").strip() or "(empty)"
        print()
        print(f"{idx}. {agent} :: {task_name}")
        print("-" * 72)
        print(textwrap.indent(raw, "  "))
    print()
    print("Event Timeline")
    print("--------------")
    events = list(out.get("events") or [])
    if not events:
        print("(none)")
    for item in events:
        print(_format_team_watch_event(item))


def cmd_team_bug_scan_live(args: argparse.Namespace) -> None:
    team_id = _norm(getattr(args, "team_id", "") or "")
    if not team_id:
        raise RuntimeError("team_id is required")
    container = str(getattr(args, "container", "") or "").strip() or _default_runtime_control_plane_container()
    cmd = [
        "docker",
        "exec",
        "-i",
        container,
        "python",
        "/openteam/scripts/runtime/team_bug_scan_live.py",
        "--team-id",
        team_id,
        "--target-id",
        str(getattr(args, "target_id", "") or "").strip(),
    ]
    project_id = str(getattr(args, "project_id", "") or "").strip()
    if project_id:
        cmd.extend(["--project-id", project_id])
    if bool(getattr(args, "json", False)):
        cmd.append("--json")
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"team bug-scan-live failed rc={proc.returncode}")

def _resolve_team_run_id(base: str, *, team_id: str, project_id: str, explicit_run_id: str) -> str:
    rid = str(explicit_run_id or "").strip()
    if rid:
        return rid
    query = ""
    if str(project_id or "").strip():
        query = "?project_id=" + urllib.parse.quote(str(project_id or "").strip(), safe="")
    out = _http_json("GET", base + "/v1/runs" + query)
    runs = list(out.get("runs") or [])
    marker = f"team:{str(team_id or '').strip().lower()}"
    for item in runs:
        state = str(item.get("state") or "").strip().upper()
        objective = str(item.get("objective") or "").strip().lower()
        if state == "RUNNING" and marker in objective:
            rid = str(item.get("run_id") or "").strip()
            if rid:
                return rid
    status = _team_status_doc(base_url=base)
    team = _team_summary_from_status(status, team_id=team_id)
    last_run = team.get("last_run") if isinstance(team, dict) else {}
    if isinstance(last_run, dict):
        rid = str(last_run.get("run_id") or "").strip()
    return rid


def _format_team_watch_event(item: dict[str, Any]) -> str:
    event_type = str(item.get("event_type") or "")
    actor = str(item.get("actor") or "")
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    if event_type.endswith("_PLANNING_TASK_OUTPUT"):
        agent = str(payload.get("agent") or "agent")
        task_name = str(payload.get("task_name") or "task")
        raw = str(payload.get("raw") or "(empty)").rstrip()
        return "\n".join([f"[planning] {agent} :: {task_name}", textwrap.indent(raw or "(empty)", "  ")])
    detail_parts = []
    for key in ("stage", "reason", "lane", "workflow_id", "title", "status", "records", "bug_findings", "proposal_id", "task_id", "target_id", "module", "state", "action"):
        value = payload.get(key)
        if value in ("", None, [], {}):
            continue
        detail_parts.append(f"{key}={value}")
    detail = "; ".join(detail_parts)
    if not detail and payload:
        raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        detail = raw_payload if len(raw_payload) <= 220 else raw_payload[:217] + "..."
    line = f"[event] {str(item.get('ts') or '')} {event_type}"
    if actor:
        line += f" actor={actor}"
    if detail:
        line += f" :: {detail}"
    return line


def _default_runtime_control_plane_container() -> str:
    proc = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.service=control-plane",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker ps failed: {(proc.stderr or proc.stdout or '').strip()[:300]}")
    names = [str(line).strip() for line in (proc.stdout or "").splitlines() if str(line).strip()]
    if not names:
        raise RuntimeError("No running control-plane container found. Start the runtime first.")
    for name in names:
        if "control-plane" in name:
            return name
    return names[0]


def cmd_improvement_targets(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    query: dict[str, str] = {}
    project_id = str(getattr(args, "project_id", "") or _default_project_id(prof, args)).strip()
    if project_id:
        query["project_id"] = project_id
    if bool(getattr(args, "enabled_only", False)):
        query["enabled_only"] = "1"
    url = base + "/v1/improvement/targets"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    out = _http_json("GET", url)
    targets = list(out.get("targets") or [])
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"targets_total={len(targets)}")
    if not targets:
        print("(none)")
        return
    rows: list[list[str]] = []
    for t in targets:
        rows.append(
            [
                str(t.get("target_id") or ""),
                str(t.get("project_id") or ""),
                "enabled" if bool(t.get("enabled")) else "disabled",
                str(t.get("repo_locator") or "")[:48],
                str(t.get("repo_root") or "")[:56],
                str(t.get("display_name") or "")[:40],
            ]
        )
    print(_fmt_table(["target_id", "project_id", "state", "repo_locator", "repo_root", "display_name"], rows))


def cmd_improvement_target_add(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    repo_path = str(getattr(args, "repo_path", "") or "").strip()
    payload = {
        "target_id": str(getattr(args, "target_id", "") or "").strip() or None,
        "project_id": _default_project_id(prof, args),
        "display_name": str(getattr(args, "display_name", "") or "").strip() or None,
        "repo_path": str(Path(repo_path).expanduser().resolve()) if repo_path else None,
        "repo_url": str(getattr(args, "repo_url", "") or "").strip() or None,
        "repo_locator": str(getattr(args, "repo_locator", "") or "").strip() or None,
        "default_branch": str(getattr(args, "default_branch", "") or "").strip() or None,
        "enabled": not bool(getattr(args, "disable", False)),
        "auto_discovery": bool(getattr(args, "auto_discovery", False)),
        "auto_delivery": bool(getattr(args, "auto_delivery", False)),
        "ship_enabled": bool(getattr(args, "ship_enabled", False)),
        "workstream_id": str(getattr(args, "workstream", "") or "general").strip() or "general",
    }
    out = _http_json("POST", base + "/v1/improvement/targets", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    target = out.get("target") or {}
    print(f"ok={bool(out.get('ok'))}")
    print(f"target_id={target.get('target_id','')}")
    print(f"project_id={target.get('project_id','')}")
    print(f"repo_locator={target.get('repo_locator','')}")
    print(f"repo_root={target.get('repo_root','')}")


def cmd_openclaw_status(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    out = _http_json("GET", base + "/v1/openclaw/status")
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"openclaw.available={bool(out.get('available'))}")
    print(f"openclaw.enabled={bool(out.get('enabled'))}")
    print(f"openclaw.configured={bool(out.get('configured'))}")
    print(f"openclaw.bin_path={str(out.get('bin_path') or '')}")
    print(f"openclaw.config_file={str(out.get('config_file') or '')}")
    print(f"openclaw.channel={str(out.get('channel') or '')}")
    print(f"openclaw.target={str(out.get('target') or '')}")
    print(f"openclaw.gateway_mode={str(out.get('gateway_mode') or '')}")
    print(f"openclaw.gateway_url={str(out.get('gateway_url') or '')}")
    print(f"openclaw.gateway_transport={str(out.get('gateway_transport') or '')}")
    print(f"openclaw.gateway_state_dir={str(out.get('gateway_state_dir') or '')}")
    print(f"openclaw.allow_insecure_private_ws={bool(out.get('allow_insecure_private_ws'))}")
    print(f"openclaw.path_patterns={','.join([str(x) for x in (out.get('path_patterns') or [])])}")
    print(f"openclaw.event_types={','.join([str(x) for x in (out.get('event_types') or [])])}")
    health = out.get("health") or {}
    print(f"openclaw.health_ok={bool(health.get('ok'))}")
    state = out.get("state") or {}
    print(f"openclaw.cursor={state.get('cursor', 0)}")
    print(f"openclaw.last_run_at={state.get('last_run_at', '')}")
    print(f"openclaw.last_error={state.get('last_error', '')}")


def cmd_openclaw_config(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    enabled = None
    if bool(getattr(args, "enable", False)):
        enabled = True
    elif bool(getattr(args, "disable", False)):
        enabled = False
    payload = {
        "enabled": enabled,
        "channel": str(getattr(args, "channel", "") or "").strip() or None,
        "target": str(getattr(args, "target", "") or "").strip() or None,
        "gateway_mode": str(getattr(args, "gateway_mode", "") or "").strip() or None,
        "gateway_url": str(getattr(args, "gateway_url", "") or "").strip() or None,
        "gateway_token": str(getattr(args, "gateway_token", "") or "").strip() or None,
        "gateway_password": str(getattr(args, "gateway_password", "") or "").strip() or None,
        "gateway_transport": str(getattr(args, "gateway_transport", "") or "").strip() or None,
        "gateway_state_dir": str(getattr(args, "gateway_state_dir", "") or "").strip() or None,
        "allow_insecure_private_ws": True if bool(getattr(args, "allow_insecure_private_ws", False)) else (False if bool(getattr(args, "disallow_insecure_private_ws", False)) else None),
        "path_patterns": [str(x).strip() for x in (getattr(args, "path", []) or []) if str(x).strip()] or None,
        "event_types": [str(x).strip() for x in (getattr(args, "event_type", []) or []) if str(x).strip()] or None,
        "exclude_event_types": [str(x).strip() for x in (getattr(args, "exclude_event_type", []) or []) if str(x).strip()] or None,
        "message_prefix": str(getattr(args, "message_prefix", "") or "").strip() or None,
    }
    out = _http_json("POST", base + "/v1/openclaw/config", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    cfg = out.get("config") or {}
    print(f"openclaw.enabled={bool(cfg.get('enabled'))}")
    print(f"openclaw.channel={str(cfg.get('channel') or '')}")
    print(f"openclaw.target={str(cfg.get('target') or '')}")
    print(f"openclaw.gateway_mode={str(cfg.get('gateway_mode') or '')}")
    print(f"openclaw.gateway_url={str(cfg.get('gateway_url') or '')}")
    print(f"openclaw.gateway_transport={str(cfg.get('gateway_transport') or '')}")
    print(f"openclaw.gateway_state_dir={str(cfg.get('gateway_state_dir') or '')}")
    print(f"openclaw.allow_insecure_private_ws={bool(cfg.get('allow_insecure_private_ws'))}")
    print(f"openclaw.path_patterns={','.join([str(x) for x in (cfg.get('path_patterns') or [])])}")
    print(f"openclaw.event_types={','.join([str(x) for x in (cfg.get('event_types') or [])])}")


def cmd_openclaw_test(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    payload = {
        "message": str(getattr(args, "message", "") or "").strip() or "OpenTeam OpenClaw test message",
        "channel": str(getattr(args, "channel", "") or "").strip() or None,
        "target": str(getattr(args, "target", "") or "").strip() or None,
        "path": str(getattr(args, "path", "") or "").strip() or None,
        "dry_run": bool(getattr(args, "dry_run", False)),
    }
    out = _http_json("POST", base + "/v1/openclaw/report/test", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"ok={bool(out.get('ok'))}")
    print(f"channel={str(out.get('channel') or '')}")
    print(f"target={str(out.get('target') or '')}")
    print(out.get("message") or "")


def cmd_openclaw_sweep(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    payload = {
        "dry_run": bool(getattr(args, "dry_run", False)),
        "limit": int(getattr(args, "limit", 100) or 100),
    }
    out = _http_json("POST", base + "/v1/openclaw/sweep", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"ok={bool(out.get('ok'))}")
    print(f"scanned={out.get('scanned', 0)}")
    print(f"sent={out.get('sent', 0)}")
    print(f"skipped={out.get('skipped', 0)}")
    errs = out.get("errors") or []
    print(f"errors={len(errs)}")


def cmd_daemon_start(args: argparse.Namespace) -> None:
    """
    Legacy daemon mode has been removed.
    """
    raise RuntimeError("Legacy team daemon has been removed. Start the OpenTeam runtime or run `openteam team run --team-id <team_id> --force`.")


def cmd_daemon_stop(args: argparse.Namespace) -> None:
    print("legacy_team_daemon.removed=true")


def cmd_daemon_status(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    base, _ = _base_url(args)
    status = _team_status_doc(base_url=base)
    team_id = _default_team_id_from_status(status)
    last = _read_last_team_run(repo_root, base_url=base, team_id=team_id)
    payload = {
        "legacy_team_daemon": "removed",
        "default_team_id": team_id,
        "runtime_state_root": str(_runtime_root_for_repo(repo_root) / "state"),
        "last_team_run": last,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_audit_deterministic_gov(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
    ]
    if getattr(args, "out", ""):
        argv += ["--out", str(args.out).strip()]
    # Pass through CLI profile (used by the audit generator to run doctor/daemon status consistently).
    if getattr(args, "profile", ""):
        argv += ["--profile", str(args.profile).strip()]
    _run_pipeline(repo_root, "scripts/pipelines/audit_deterministic_gov.py", argv)


def cmd_audit_execution_strategy(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
    ]
    if getattr(args, "out", ""):
        argv += ["--out", str(args.out).strip()]
    if getattr(args, "profile", ""):
        argv += ["--profile", str(args.profile).strip()]
    _run_pipeline(repo_root, "scripts/pipelines/audit_execution_strategy.py", argv)


def cmd_audit_reqv3_locks(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
    ]
    if getattr(args, "out", ""):
        argv += ["--out", str(args.out).strip()]
    if getattr(args, "project_id", ""):
        argv += ["--project-id", str(args.project_id).strip()]
    if bool(getattr(args, "skip_team", False)):
        argv.append("--skip-team")
    if bool(getattr(args, "skip_db", False)):
        argv.append("--skip-db")
    _run_pipeline(repo_root, "scripts/pipelines/audit_reqv3_locks.py", argv)


def _parse_metrics_jsonl(path: Path) -> list[str]:
    issues: list[str] = []
    if not path.exists():
        return [f"missing: {path}"]
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                issues.append(f"{path}:{i} not an object")
                continue
            for k in ("ts", "event_type", "actor"):
                if not str(obj.get(k) or "").strip():
                    issues.append(f"{path}:{i} missing field: {k}")
        except Exception as e:
            issues.append(f"{path}:{i} invalid json: {e}")
    return issues


def cmd_metrics_check(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")

    required_schema = repo_root / "schemas" / "telemetry_event.schema.json"
    if not required_schema.exists():
        print(f"FAIL missing_schema={required_schema}")
        raise SystemExit(2)

    tasks_root = repo_root / ".openteam" / "logs" / "tasks"
    if not tasks_root.exists():
        print(f"FAIL missing_tasks_logs_dir={tasks_root}")
        raise SystemExit(2)

    want_logs = [
        "00_intake.md",
        "01_plan.md",
        "02_todo.md",
        "03_work.md",
        "04_test.md",
        "05_release.md",
        "06_observe.md",
        "07_retro.md",
    ]

    missing_files: list[str] = []
    metrics_issues: list[str] = []
    checked_tasks = 0

    for d in sorted(tasks_root.iterdir()):
        if not d.is_dir():
            continue
        checked_tasks += 1
        for f in want_logs:
            if not (d / f).exists():
                missing_files.append(f"{d.name}/{f}")
        metrics_issues.extend(_parse_metrics_jsonl(d / "metrics.jsonl"))

    ok = (not missing_files) and (not metrics_issues)
    print(f"ok={ok} tasks_checked={checked_tasks} missing_files={len(missing_files)} metrics_issues={len(metrics_issues)}")
    if missing_files and not args.quiet:
        print("missing:")
        for x in missing_files[:50]:
            print(f"- {x}")
    if metrics_issues and not args.quiet:
        print("metrics_issues:")
        for x in metrics_issues[:50]:
            print(f"- {x}")
    if not ok:
        raise SystemExit(2)


def cmd_metrics_analyze(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    script = repo_root / "scripts" / "metrics" / "analyze_evolution.py"
    if not script.exists():
        raise RuntimeError(f"missing metrics analyzer: {script}")
    p = subprocess.run([sys.executable, str(script), "--tasks-dir", str(repo_root / ".openteam" / "logs" / "tasks")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (p.stderr or b"").decode("utf-8", errors="replace").strip()
    if p.returncode != 0:
        raise RuntimeError(f"metrics analyze failed: {err[:200]}")
    print(out)


def cmd_metrics_bootstrap(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    script = repo_root / "scripts" / "migrations" / "bootstrap_task_artifacts.py"
    if not script.exists():
        raise RuntimeError(f"missing bootstrap script: {script}")
    argv = [sys.executable, str(script), "--full"]
    if args.dry_run:
        argv.append("--dry-run")
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sys.stdout.write((p.stdout or b"").decode("utf-8", errors="replace"))
    if p.returncode != 0:
        sys.stderr.write((p.stderr or b"").decode("utf-8", errors="replace"))
        raise SystemExit(p.returncode)


def cmd_cluster_status(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    out = _http_json("GET", base + "/v1/cluster/status", timeout_sec=10)
    leader = out.get("leader") or {}
    nodes = out.get("nodes") or []
    pending = out.get("pending_decisions") or []
    llm = out.get("llm_profile") or {}
    qual = out.get("leader_qualification") or {}
    print(f"profile={prof['name']} base_url={base}")
    print(f"leader.instance_id={leader.get('leader_instance_id','')}")
    print(f"leader.backend={leader.get('backend','')}")
    if llm:
        if llm.get("provider"):
            print(f"llm.provider={llm.get('provider')}")
        if llm.get("model_id"):
            print(f"llm.model_id={llm.get('model_id')}")
        if llm.get("auth_mode"):
            print(f"llm.auth_mode={llm.get('auth_mode')}")
    if qual:
        print(f"leader_qualification.qualified={qual.get('qualified')}")
        if qual.get("reason"):
            print(f"leader_qualification.reason={qual.get('reason')}")
    if leader.get("leader_base_url"):
        print(f"leader.base_url={leader.get('leader_base_url')}")
    if pending:
        print(f"PENDING_DECISIONS={len(pending)}")
    print(f"nodes={len(nodes)}")
    if nodes:
        rows = []
        for n in nodes[:50]:
            rows.append(
                [
                    str(n.get("instance_id", ""))[:8],
                    str(n.get("role_preference", "")),
                    str(n.get("heartbeat_at", "")),
                    ",".join(n.get("capabilities") or [])[:30],
                    ",".join(n.get("tags") or [])[:30],
                ]
            )
        print(_fmt_table(["node", "role_pref", "heartbeat", "capabilities", "tags"], rows))


def cmd_cluster_qualify(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _run_pipeline(
        repo_root,
        "scripts/pipelines/cluster_election.py",
        ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "qualify"],
    )


def cmd_node_join_script(args: argparse.Namespace) -> None:
    # Print a join command to run on the new server (no secrets included).
    base, prof = _base_url(args)
    brain_url = args.brain_base_url or base
    cluster_repo = args.cluster_repo
    if not cluster_repo:
        raise RuntimeError("missing --cluster-repo owner/name")
    caps = args.capabilities or ""
    tags = args.tags or ""
    role = args.role or "auto"
    print(
        f'bash scripts/cluster/join_node.sh --cluster-repo "{cluster_repo}" --brain-base-url "{brain_url}" --role "{role}" --capabilities "{caps}" --tags "{tags}"'
    )


def cmd_node_add(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Run from within the repo or set OPENTEAM_REPO_PATH.")
    script = repo_root / "scripts" / "cluster" / "bootstrap_remote_node.sh"
    if not script.exists():
        raise RuntimeError(f"missing script: {script}")
    base, prof = _base_url(args)
    argv = [
        "bash",
        str(script),
        "--host",
        args.host,
        "--user",
        args.user,
        "--cluster-repo",
        args.cluster_repo,
        "--brain-base-url",
        args.brain_base_url or base,
        "--role",
        args.role or "auto",
        "--capabilities",
        args.capabilities or "",
        "--tags",
        args.tags or "",
    ]
    if args.ssh_key:
        argv += ["--ssh-key", args.ssh_key]
    ws_root = _workspace_root(args)
    child_env: Optional[dict[str, str]] = None
    stdin_password = ""
    if bool(getattr(args, "password_stdin", False)):
        stdin_password = sys.stdin.read().rstrip("\r\n")
        if not stdin_password:
            raise RuntimeError("--password-stdin was provided but stdin was empty")
        argv += ["--password-stdin"]
        child_env = dict(os.environ)
        child_env["OPENTEAM_SSH_PASSWORD"] = stdin_password
    if args.execute:
        _approval_gate(
            args,
            repo_root=repo_root,
            action_kind="node_add_execute",
            summary=f"node add --execute host={args.host} user={args.user} cluster_repo={args.cluster_repo}",
            payload={
                "host": args.host,
                "user": args.user,
                "cluster_repo": args.cluster_repo,
                "brain_base_url": args.brain_base_url or base,
                "role": args.role or "auto",
                "capabilities": args.capabilities or "",
                "tags": args.tags or "",
                "ssh_key": args.ssh_key or "",
                "password_stdin": bool(getattr(args, "password_stdin", False)),
                "push_hub_config": bool(getattr(args, "push_hub_config", False)),
                "hub_host": str(getattr(args, "hub_host", "") or ""),
                "remote_env_path": str(getattr(args, "remote_env_path", "") or "~/.openteam/node.env"),
            },
        )
        argv += ["--execute"]
    p = subprocess.run(
        argv,
        check=False,
        env=child_env or None,
        input=(stdin_password + "\n") if bool(getattr(args, "password_stdin", False)) else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sys.stdout.write(p.stdout or "")
    sys.stderr.write(p.stderr or "")
    _record_installer_run(
        repo_root=repo_root,
        workspace_root=ws_root,
        component="node_add.bootstrap",
        stage="bootstrap_remote_node",
        target_host=str(args.host),
        ok=(p.returncode == 0),
        stdout_text=str(p.stdout or ""),
        stderr_text=str(p.stderr or ""),
    )
    if p.returncode != 0:
        raise SystemExit(p.returncode)
    # Optional: push Brain hub config to the new node.
    if bool(getattr(args, "execute", False)) and bool(getattr(args, "push_hub_config", False)):
        argv2 = [
            "--repo-root",
            str(repo_root),
            "--workspace-root",
            str(ws_root),
            "--host",
            str(args.host),
            "--user",
            str(args.user),
            "--remote-env-path",
            str(getattr(args, "remote_env_path", "") or "~/.openteam/node.env"),
        ]
        if str(getattr(args, "hub_host", "") or "").strip():
            argv2 += ["--hub-host", str(args.hub_host).strip()]
        if str(getattr(args, "ssh_key", "") or "").strip():
            argv2 += ["--ssh-key", str(args.ssh_key).strip()]
        env2 = None
        if bool(getattr(args, "password_stdin", False)):
            argv2.append("--password-stdin")
            env2 = dict(os.environ)
            env2["OPENTEAM_SSH_PASSWORD"] = stdin_password
        _approval_gate(
            args,
            repo_root=repo_root,
            action_kind="hub_push_config_with_secrets",
            summary=f"node add push hub config host={args.host} user={args.user}",
            payload={"host": args.host, "user": args.user, "remote_env_path": str(getattr(args, 'remote_env_path', '') or '~/.openteam/node.env')},
        )
        hub_push_script = (repo_root / "scripts" / "pipelines" / "hub_push_config.py").resolve()
        if not hub_push_script.exists():
            raise RuntimeError(f"missing pipeline: {hub_push_script}")
        p2 = subprocess.run(
            [sys.executable, str(hub_push_script)] + argv2,
            check=False,
            env=env2 or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        sys.stdout.write(p2.stdout or "")
        sys.stderr.write(p2.stderr or "")
        stage = _extract_stage_from_json_output(str(p2.stdout or ""), default="hub_push_config")
        _record_installer_run(
            repo_root=repo_root,
            workspace_root=ws_root,
            component="node_add.push_hub_config",
            stage=stage,
            target_host=str(args.host),
            ok=(p2.returncode == 0),
            stdout_text=str(p2.stdout or ""),
            stderr_text=str(p2.stderr or ""),
        )
        if p2.returncode != 0:
            raise SystemExit(p2.returncode)


def cmd_repo_create(args: argparse.Namespace) -> None:
    if shutil_which("gh") is None:
        raise RuntimeError("gh CLI not found. Install gh then run: gh auth login")

    name = args.name
    org = args.org or ""
    full = f"{org}/{name}" if org else name
    vis = "--public" if bool(getattr(args, "public", False)) else "--private"
    cmd = ["gh", "repo", "create", full, vis]
    if args.clone_dir:
        cmd += ["--clone", "--", str(args.clone_dir)]

    if not args.approve:
        print("approval_required: repo_create is high risk")
        print("would_run: " + " ".join(cmd))
        print("next: re-run with --approve to execute (will prompt + record approval)")
        return

    # Approval gate (records to DB when OPENTEAM_DB_URL is set; otherwise local audit fallback).
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    _approval_gate(
        args,
        repo_root=repo_root,
        action_kind="repo_create",
        summary=f"gh repo create {full} {vis}",
        payload={"full": full, "visibility": vis, "clone_dir": str(args.clone_dir or "")},
    )

    p = subprocess.run(cmd, check=False)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def cmd_task_new(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    cfg = _load_config()
    prof = _get_profile(cfg, args.profile)
    scope = _default_scope(prof, args)
    if getattr(args, "scope", ""):
        scope = str(args.scope).strip()

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--scope",
        scope,
        "--title",
        args.title,
        "--workstreams",
        args.workstreams or "general",
        "--risk-level",
        getattr(args, "risk_level", "") or "R1",
        "--mode",
        args.mode or "auto",
    ]
    if bool(args.dry_run):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/task_create.py", argv)

    # Hook: project bootstrap/upgrade should ensure project repo AGENTS.md contains Team-OS manual block.
    if str(scope or "").strip().startswith("project:"):
        pid = _require_project_id(str(scope).split(":", 1)[1])
        mode = str(args.mode or "").strip().lower()
        if mode in ("bootstrap", "upgrade"):
            _ensure_project_scaffold(_workspace_root(args), pid)
            _inject_project_agents_manual(args, project_id=pid, reason=f"task_new_{mode}")


def cmd_task_close(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        str(args.task_id),
    ]
    if getattr(args, "scope", ""):
        argv += ["--scope", str(args.scope).strip()]
    if bool(getattr(args, "skip_tests", False)):
        argv.append("--skip-tests")
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/task_close.py", argv)


def cmd_task_ship(args: argparse.Namespace) -> None:
    """
    Enforce close -> commit -> push discipline for one task.
    """
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        str(args.task_id),
    ]
    if getattr(args, "scope", ""):
        argv += ["--scope", str(args.scope).strip()]
    if getattr(args, "summary", ""):
        argv += ["--summary", str(args.summary).strip()]
    if getattr(args, "base", ""):
        argv += ["--base", str(args.base).strip()]
    if bool(getattr(args, "no_pr", False)):
        argv.append("--no-pr")
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/task_ship.py", argv)


def cmd_prompt_compile(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    cfg = _load_config()
    prof = _get_profile(cfg, args.profile)
    scope = _default_scope(prof, args)
    if getattr(args, "scope", ""):
        scope = str(args.scope).strip()
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--scope", scope]
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/prompt_compile.py", argv)


def cmd_prompt_diff(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    cfg = _load_config()
    prof = _get_profile(cfg, args.profile)
    scope = _default_scope(prof, args)
    if getattr(args, "scope", ""):
        scope = str(args.scope).strip()
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--scope", scope]
    _run_pipeline(repo_root, "scripts/pipelines/prompt_diff.py", argv)


def cmd_task_resume(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    payload = {"task_id": args.task_id or None, "all": bool(args.all)}
    out = _http_json("POST", base + "/v1/recovery/resume", payload, timeout_sec=120)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def shutil_which(cmd: str) -> Optional[str]:
    import shutil

    return shutil.which(cmd)


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]

    # Project directory convenience mode:
    # when invoked as `openteam` (no subcommand) inside <workspace>/projects/<id>/repo,
    # enter a raw-requirement REPL.
    if not argv:
        cfg: dict[str, Any] = {}
        try:
            cfg = _load_config()
        except Exception:
            cfg = {}
        cur = Path.cwd().resolve()
        ws_candidates = [_workspace_root_from_cfg(cfg)]
        for p in [cur] + list(cur.parents):
            if p.name == "workspace":
                ws_candidates.append(p)
        seen: set[str] = set()
        for ws in ws_candidates:
            k = str(ws.resolve())
            if k in seen:
                continue
            seen.add(k)
            try:
                pid = _detect_workspace_project_from_cwd(ws, cwd=cur)
            except Exception:
                continue
            if pid:
                tmp = argparse.Namespace(profile=None, workspace_root=str(ws))
                try:
                    return int(_project_repl(tmp, project_id=pid))
                except Exception as e:
                    eprint(f"ERROR: {e}")
                    return 2

    p = argparse.ArgumentParser(prog="openteam", description="OpenTeam CLI (Control Plane client)")
    p.add_argument("--profile", help="profile name (from ~/.openteam/config.toml)")
    p.add_argument("--workspace-root", help="override workspace root (default: ~/.openteam/workspace or config)")

    sp = p.add_subparsers(dest="cmd", required=True)

    # config
    cfg = sp.add_parser("config")
    cfg_sp = cfg.add_subparsers(dest="subcmd", required=True)
    cfg_sp.add_parser("init").set_defaults(fn=cmd_config_init)
    ap = cfg_sp.add_parser("add-profile")
    ap.add_argument("name")
    ap.add_argument("base_url")
    ap.add_argument("--default-project-id")
    ap.set_defaults(fn=cmd_config_add_profile)
    up = cfg_sp.add_parser("use")
    up.add_argument("name")
    up.set_defaults(fn=cmd_config_use)
    cfg_sp.add_parser("show").set_defaults(fn=cmd_config_show)

    # workspace
    ws = sp.add_parser("workspace", help="Local Workspace (project truth sources live outside the repo)")
    ws_sp = ws.add_subparsers(dest="subcmd", required=True)
    wi = ws_sp.add_parser("init", help="Initialize workspace directories (idempotent)")
    wi.add_argument("--path", help="workspace root path (default from config)")
    wi.set_defaults(fn=cmd_workspace_init)
    ws_sp.add_parser("show", help="Show workspace root and project summary").set_defaults(fn=cmd_workspace_show)
    ws_sp.add_parser("doctor", help="Validate workspace structure and permissions").set_defaults(fn=cmd_workspace_doctor)
    wm = ws_sp.add_parser("migrate", help="Migrate legacy project artifacts out of the openteam repo (default dry-run)")
    wm.add_argument("--from-repo", action="store_true", help="scan current openteam repo for legacy project artifacts")
    wm.add_argument("--dry-run", action="store_true", help="plan only (default)")
    wm.add_argument("--force", action="store_true", help="apply moves (high risk; requires your approval)")
    wm.add_argument("--yes", action="store_true", help="skip interactive confirmation (still high risk)")
    wm.set_defaults(fn=cmd_workspace_migrate)

    # projects (workspace)
    pr = sp.add_parser("project", help="Workspace projects")
    pr_sp = pr.add_subparsers(dest="subcmd", required=True)
    pr_sp.add_parser("list", help="List projects under workspace").set_defaults(fn=cmd_project_list)

    # project config (Workspace-local; deterministic)
    pcfg = pr_sp.add_parser("config", help="Project config (Workspace-local; schema-validated)")
    pcfg_sp = pcfg.add_subparsers(dest="config_cmd", required=True)
    pci = pcfg_sp.add_parser("init", help="Create default project.yaml if missing (idempotent)")
    pci.add_argument("--project", required=True)
    pci.add_argument("--dry-run", action="store_true")
    pci.set_defaults(fn=cmd_project_config_init)
    pcs = pcfg_sp.add_parser("show", help="Show project.yaml")
    pcs.add_argument("--project", required=True)
    pcs.set_defaults(fn=cmd_project_config_show)
    pset = pcfg_sp.add_parser("set", help="Set a config key (dot-path) and validate schema")
    pset.add_argument("--project", required=True)
    pset.add_argument("--key", required=True)
    pset.add_argument("--value", required=True)
    pset.add_argument("--dry-run", action="store_true")
    pset.set_defaults(fn=cmd_project_config_set)
    pv = pcfg_sp.add_parser("validate", help="Validate project.yaml against schema")
    pv.add_argument("--project", required=True)
    pv.set_defaults(fn=cmd_project_config_validate)

    # project AGENTS.md injection (idempotent)
    pag = pr_sp.add_parser("agents", help="Project repo AGENTS.md (Team-OS manual injection)")
    pag_sp = pag.add_subparsers(dest="agents_cmd", required=True)
    pinj = pag_sp.add_parser("inject", help="Inject/Update Team-OS manual block into project repo AGENTS.md")
    pinj.add_argument("--project", required=True)
    pinj.add_argument("--repo-path", dest="repo_path", help="override project repo path (default: <workspace>/projects/<id>/repo)")
    pinj.add_argument("--dry-run", action="store_true")
    pinj.set_defaults(fn=cmd_project_agents_inject)

    st = sp.add_parser("status")
    st.add_argument("--project")
    st.add_argument("--workstream")
    st.add_argument("--all-decisions", action="store_true", help="show pending decisions for all projects (default: filter to selected project)")
    st.set_defaults(fn=cmd_status)

    fc = sp.add_parser("focus")
    fc.add_argument("--set")
    fc.set_defaults(fn=cmd_focus)

    ag = sp.add_parser("agents")
    ag.add_argument("--all", action="store_true")
    ag.add_argument("--project")
    ag.add_argument("--workstream")
    ag.add_argument("--state")
    ag.add_argument("--role")
    ag.set_defaults(fn=cmd_agents)

    tk = sp.add_parser("tasks")
    tk.add_argument("--all", action="store_true")
    tk.add_argument("--project")
    tk.add_argument("--workstream")
    tk.add_argument("--state")
    tk.add_argument("--limit", type=int, default=50)
    tk.add_argument("--offset", type=int, default=0)
    tk.set_defaults(fn=cmd_tasks)

    pn = sp.add_parser("panel", help="GitHub Projects panel (view layer)")
    pn_sp = pn.add_subparsers(dest="subcmd", required=True)
    pshow = pn_sp.add_parser("show")
    pshow.add_argument("--project")
    pshow.set_defaults(fn=cmd_panel_show)
    popen = pn_sp.add_parser("open")
    popen.add_argument("--project")
    popen.set_defaults(fn=cmd_panel_open)
    phealth = pn_sp.add_parser("health")
    phealth.add_argument("--project")
    phealth.set_defaults(fn=cmd_panel_health)
    ps = pn_sp.add_parser("sync")
    ps.add_argument("--project")
    ps.add_argument("--full", action="store_true", help="full sync (ensure fields); otherwise incremental")
    ps.add_argument("--dry-run", action="store_true", help="compute planned actions only (no GitHub calls)")
    ps.set_defaults(fn=cmd_panel_sync)

    cl = sp.add_parser("cluster", help="Multi-node cluster status (Brain/Assistant)")
    cl_sp = cl.add_subparsers(dest="subcmd", required=True)
    cl_sp.add_parser("status").set_defaults(fn=cmd_cluster_status)
    cl_sp.add_parser("qualify", help="Check central Brain model allowlist qualification (local)").set_defaults(fn=cmd_cluster_qualify)

    nd = sp.add_parser("node", help="Cluster node operations")
    nd_sp = nd.add_subparsers(dest="subcmd", required=True)
    na = nd_sp.add_parser("add", help="Bootstrap a remote node via SSH (default: dry-run)")
    na.add_argument("--host", required=True)
    na.add_argument("--user", required=True)
    na.add_argument("--ssh-key")
    na.add_argument("--password-stdin", action="store_true", help="read SSH password from stdin (requires sshpass on local host)")
    na.add_argument("--cluster-repo", required=True, help="owner/name for cluster bus repo")
    na.add_argument("--brain-base-url", help="override brain control-plane base url")
    na.add_argument("--role", default="auto")
    na.add_argument("--capabilities", default="")
    na.add_argument("--tags", default="")
    na.add_argument("--push-hub-config", action="store_true", help="after bootstrap, push Brain hub DB/Redis config to node")
    na.add_argument("--hub-host", default="", help="override advertised hub host/ip when pushing config")
    na.add_argument("--remote-env-path", default="~/.openteam/node.env", help="remote env path for hub config")
    na.add_argument("--execute", action="store_true", help="execute ssh/scp (high risk; requires your approval)")
    na.set_defaults(fn=cmd_node_add)
    nj = nd_sp.add_parser("join-script", help="Print a join command to run on the new server")
    nj.add_argument("--cluster-repo", required=True, help="owner/name for cluster bus repo")
    nj.add_argument("--brain-base-url", help="override brain control-plane base url")
    nj.add_argument("--role", default="auto")
    nj.add_argument("--capabilities", default="")
    nj.add_argument("--tags", default="")
    nj.set_defaults(fn=cmd_node_join_script)

    hb = sp.add_parser("hub", help="Local/central Hub operations (Postgres + Redis)")
    hb_sp = hb.add_subparsers(dest="subcmd", required=True)
    hinit = hb_sp.add_parser("init", help="Initialize hub structure and compose files")
    hinit.add_argument("--pg-port", type=int, default=5432)
    hinit.add_argument("--redis-port", type=int, default=6379)
    hinit.set_defaults(fn=cmd_hub_init)
    hb_sp.add_parser("up", help="Start hub containers").set_defaults(fn=cmd_hub_up)
    hb_sp.add_parser("down", help="Stop hub containers").set_defaults(fn=cmd_hub_down)
    hb_sp.add_parser("status", help="Show hub status").set_defaults(fn=cmd_hub_status)
    hlogs = hb_sp.add_parser("logs", help="Show hub logs")
    hlogs.add_argument("--service", choices=["postgres", "redis"], default="")
    hlogs.add_argument("--tail", type=int, default=200)
    hlogs.set_defaults(fn=cmd_hub_logs)
    hb_sp.add_parser("migrate", help="Apply DB migrations to hub Postgres").set_defaults(fn=cmd_hub_migrate)
    hexp = hb_sp.add_parser("expose", help="Expose hub to selected CIDRs (HIGH risk)")
    hexp.add_argument("--bind-ip", required=True)
    hexp.add_argument("--allow-cidrs", required=True, help="comma separated CIDRs")
    hexp.add_argument("--open-redis", action="store_true")
    hexp.set_defaults(fn=cmd_hub_expose)
    hbak = hb_sp.add_parser("backup", help="Backup hub Postgres")
    hbak.add_argument("--output", default="")
    hbak.set_defaults(fn=cmd_hub_backup)
    hres = hb_sp.add_parser("restore", help="Restore hub Postgres backup (HIGH risk)")
    hres.add_argument("--file", required=True)
    hres.set_defaults(fn=cmd_hub_restore)
    hec = hb_sp.add_parser("export-config", help="Export non-secret hub config")
    hec.add_argument("--format", choices=["env", "yaml"], default="env")
    hec.set_defaults(fn=cmd_hub_export_config)
    hpc = hb_sp.add_parser("push-config", help="Push hub connection config to remote node (HIGH risk)")
    hpc.add_argument("--host", required=True)
    hpc.add_argument("--user", required=True)
    hpc.add_argument("--ssh-key", default="")
    hpc.add_argument("--password-stdin", action="store_true")
    hpc.add_argument("--hub-host", default="", help="override advertised hub host/ip")
    hpc.add_argument("--remote-env-path", default="~/.openteam/node.env")
    hpc.set_defaults(fn=cmd_hub_push_config)

    rp = sp.add_parser("repo", help="Repo operations (GitHub)")
    rp_sp = rp.add_subparsers(dest="subcmd", required=True)
    rc = rp_sp.add_parser("create", help="Create a new GitHub repo (high risk; requires --approve)")
    rc.add_argument("--name", required=True)
    rc.add_argument("--org")
    rc_vis = rc.add_mutually_exclusive_group()
    rc_vis.add_argument("--public", action="store_true", help="create public repo")
    rc_vis.add_argument("--private", action="store_true", help="create private repo (default)")
    rc.add_argument("--clone-dir")
    rc.add_argument("--approve", action="store_true")
    rc.set_defaults(fn=cmd_repo_create)

    ts = sp.add_parser("task", help="Task lifecycle commands")
    ts_sp = ts.add_subparsers(dest="subcmd", required=True)
    tn = ts_sp.add_parser("new", help="Create a new task (deterministic; local truth-source scaffold)")
    tn.add_argument("--title", required=True)
    tn.add_argument("--scope", help="openteam | project:<id> (default derived from --project/default_project_id)")
    tn.add_argument("--project")
    tn.add_argument("--workstreams", help="comma-separated")
    tn.add_argument("--risk-level", default="R1", help="R0|R1|R2|R3")
    tn.add_argument("--mode", default="auto", help="auto|bootstrap|upgrade")
    tn.add_argument("--dry-run", action="store_true", help="plan only (no filesystem writes)")
    tn.set_defaults(fn=cmd_task_new)
    tc = ts_sp.add_parser("close", help="Close a task (validate DoD + mark ledger closed)")
    tc.add_argument("task_id")
    tc.add_argument("--scope", help="optional: openteam | project:<id> (auto-detect if omitted)")
    tc.add_argument("--skip-tests", action="store_true")
    tc.add_argument("--dry-run", action="store_true")
    tc.set_defaults(fn=cmd_task_close)
    tsh = ts_sp.add_parser("ship", help="Ship a task (close -> gates -> commit -> push)")
    tsh.add_argument("task_id")
    tsh.add_argument("--scope", help="optional: openteam | project:<id> (default: openteam)")
    tsh.add_argument("--summary", help="commit summary (default: ledger title)")
    tsh.add_argument("--base", default="main", help="PR base branch (gh only; default: main)")
    tsh.add_argument("--no-pr", action="store_true", help="do not create PR")
    tsh.add_argument("--dry-run", action="store_true", help="plan only; do not commit/push (still runs task close + scans)")
    tsh.set_defaults(fn=cmd_task_ship)
    tr = ts_sp.add_parser("resume", help="Resume tasks after interruption (placeholder)")
    tr.add_argument("--all", action="store_true")
    tr.add_argument("--task-id")
    tr.set_defaults(fn=cmd_task_resume)

    ch = sp.add_parser("chat")
    ch.add_argument("--project")
    ch.add_argument("--workstream")
    ch.add_argument("--run")
    ch.set_defaults(fn=cmd_chat)

    req = sp.add_parser("req")
    req_sp = req.add_subparsers(dest="subcmd", required=True)
    ra = req_sp.add_parser("add")
    ra.add_argument("text")
    ra.add_argument("--scope", help="openteam | project:<id> (default derived from --project/default_project_id)")
    ra.add_argument("--project")
    ra.add_argument("--workstream")
    ra.add_argument("--priority", default="P2")
    ra.add_argument("--rationale")
    ra.add_argument("--constraints", nargs="*")
    ra.add_argument("--acceptance", nargs="*")
    ra.add_argument("--source")
    ra.set_defaults(fn=cmd_req_add)
    ri = req_sp.add_parser("import", help="Import a requirement text file (Raw-First)")
    ri.add_argument("--file", required=True)
    ri.add_argument("--scope")
    ri.add_argument("--project")
    ri.add_argument("--workstream")
    ri.set_defaults(fn=cmd_req_import)
    rl = req_sp.add_parser("list")
    rl.add_argument("--scope")
    rl.add_argument("--project")
    rl.add_argument("--show-conflicts", action="store_true")
    rl.set_defaults(fn=cmd_req_list)
    rc = req_sp.add_parser("conflicts")
    rc.add_argument("--scope")
    rc.add_argument("--project")
    rc.set_defaults(fn=cmd_req_conflicts)
    rv = req_sp.add_parser("verify", help="Run drift/conflict verification (check-only)")
    rv.add_argument("--scope")
    rv.add_argument("--project")
    rv.set_defaults(fn=cmd_req_verify)
    rb = req_sp.add_parser("rebuild", help="Re-render REQUIREMENTS.md from requirements.yaml (deterministic)")
    rb.add_argument("--scope")
    rb.add_argument("--project")
    rb.set_defaults(fn=cmd_req_rebuild)
    rbase = req_sp.add_parser("baseline", help="Baseline operations (v2 Raw-First)")
    rbase_sp = rbase.add_subparsers(dest="baseline_cmd", required=True)
    rbs = rbase_sp.add_parser("show")
    rbs.add_argument("--scope")
    rbs.add_argument("--project")
    rbs.add_argument("--max-chars", type=int, default=4000)
    rbs.set_defaults(fn=cmd_req_baseline_show)
    rb2 = rbase_sp.add_parser("set-v2", help="Propose baseline v2 (requires PM decision)")
    rb2.add_argument("text")
    rb2.add_argument("--reason", required=True)
    rb2.add_argument("--scope")
    rb2.add_argument("--project")
    rb2.set_defaults(fn=cmd_req_baseline_set_v2)

    pm = sp.add_parser("prompt", help="Prompt operations (deterministic pipelines)")
    pm_sp = pm.add_subparsers(dest="subcmd", required=True)
    pc2 = pm_sp.add_parser("compile", help="Compile MASTER_PROMPT.md deterministically")
    pc2.add_argument("--scope")
    pc2.add_argument("--project")
    pc2.add_argument("--dry-run", action="store_true")
    pc2.set_defaults(fn=cmd_prompt_compile)
    pb = pm_sp.add_parser("build", help="Build MASTER_PROMPT.md deterministically (alias of compile)")
    pb.add_argument("--scope")
    pb.add_argument("--project")
    pb.add_argument("--dry-run", action="store_true")
    pb.set_defaults(fn=cmd_prompt_compile)
    pd = pm_sp.add_parser("diff", help="Show diff for MASTER_PROMPT.md vs deterministic build output")
    pd.add_argument("--scope")
    pd.add_argument("--project")
    pd.set_defaults(fn=cmd_prompt_diff)

    mt = sp.add_parser("metrics", help="Telemetry/metrics checks and analysis (local truth source)")
    mt_sp = mt.add_subparsers(dest="subcmd", required=True)
    mc = mt_sp.add_parser("check", help="Validate task logs 00~07 + metrics.jsonl against minimal schema")
    mc.add_argument("--quiet", action="store_true")
    mc.set_defaults(fn=cmd_metrics_check)
    ma = mt_sp.add_parser("analyze", help="Analyze metrics/logs to propose evolution improvements")
    ma.set_defaults(fn=cmd_metrics_analyze)
    mb = mt_sp.add_parser("bootstrap", help="Create missing task artifacts (logs/metrics/ledger fields)")
    mb.add_argument("--dry-run", action="store_true")
    mb.set_defaults(fn=cmd_metrics_bootstrap)

    pol = sp.add_parser("policy", help="Local policy checks (no remote writes)")
    pol_sp = pol.add_subparsers(dest="subcmd", required=True)
    pc = pol_sp.add_parser("check", help="Enforce codified OpenTeam norms (no secrets, external project layout, runtime mount)")
    pc.add_argument("--json", action="store_true")
    pc.add_argument("--quiet", action="store_true")
    pc.set_defaults(fn=cmd_policy_check)

    # db (Postgres shared hub)
    db = sp.add_parser("db", help="Postgres DB (migrations)")
    db_sp = db.add_subparsers(dest="subcmd", required=True)
    dmig = db_sp.add_parser("migrate", help="Apply Postgres migrations (requires OPENTEAM_DB_URL)")
    dmig.add_argument("--db-url", help="override OPENTEAM_DB_URL")
    dmig.add_argument("--dry-run", action="store_true", help="plan only (no SQL executed)")
    dmig.set_defaults(fn=cmd_db_migrate)

    # approvals (high-risk gates)
    apv = sp.add_parser("approvals", help="High-risk approvals (DB-backed when OPENTEAM_DB_URL is set)")
    apv_sp = apv.add_subparsers(dest="subcmd", required=True)
    al = apv_sp.add_parser("list", help="List recent approvals")
    al.add_argument("--limit", type=int, default=50)
    al.set_defaults(fn=cmd_approvals_list)

    au = sp.add_parser("audit", help="Deterministic audit generators (local-only)")
    au_sp = au.add_subparsers(dest="subcmd", required=True)
    dg = au_sp.add_parser("deterministic-gov", help="Generate deterministic governance audit report (scope=openteam)")
    dg.add_argument("--out", help="override output path")
    dg.set_defaults(fn=cmd_audit_deterministic_gov)
    es = au_sp.add_parser("execution-strategy", help="Generate execution strategy audit report (PASS/FAIL/WAIVED)")
    es.add_argument("--out", help="override output path")
    es.set_defaults(fn=cmd_audit_execution_strategy)
    rl = au_sp.add_parser("reqv3-locks", help="Generate REQv3+Locks end-to-end audit report (writes docs/audits/REQV3_LOCKS_AUDIT_<ts>.md)")
    rl.add_argument("--out", help="override output path")
    rl.add_argument("--project-id", dest="project_id", default="audit-e2e")
    rl.add_argument("--skip-team", action="store_true")
    rl.add_argument("--skip-db", action="store_true")
    rl.set_defaults(fn=cmd_audit_reqv3_locks)

    doc = sp.add_parser("doctor")
    doc.set_defaults(fn=cmd_doctor)

    dm = sp.add_parser("daemon", help="Legacy daemon commands (team daemon removed)")
    dm_sp = dm.add_subparsers(dest="subcmd", required=True)
    ds = dm_sp.add_parser("start", help="Daemon mode removed; kept only as compatibility stub")
    ds.add_argument("--foreground", action="store_true", help="run in foreground (do not detach)")
    ds.set_defaults(fn=cmd_daemon_start)
    dx = dm_sp.add_parser("stop", help="Daemon mode removed; kept only as compatibility stub")
    dx.set_defaults(fn=cmd_daemon_stop)
    dy = dm_sp.add_parser("status", help="Show legacy daemon removal notice and current team runtime state")
    dy.set_defaults(fn=cmd_daemon_status)

    tm = sp.add_parser("team", help="Generic team operations")
    tm_sp = tm.add_subparsers(dest="team_cmd", required=True)
    tl = tm_sp.add_parser("list", help="List configured teams")
    tl.add_argument("--json", action="store_true")
    tl.set_defaults(fn=cmd_team_list)

    trn = tm_sp.add_parser("run", help="Run one team iteration through the control-plane")
    trn.add_argument("--team-id", required=True)
    trn.add_argument("--project")
    trn.add_argument("--workstream", default="general")
    trn.add_argument("--objective", default="")
    trn.add_argument("--target-id", default="")
    trn.add_argument("--repo-path", default="")
    trn.add_argument("--repo-url", default="")
    trn.add_argument("--repo-locator", default="")
    trn.add_argument("--dry-run", action="store_true")
    trn.add_argument("--force", action="store_true")
    trn.add_argument("--quiet", action="store_true")
    trn.set_defaults(fn=cmd_team_run)

    tw = tm_sp.add_parser("watch", help="Stream a live team run")
    tw.add_argument("--team-id", required=True)
    tw.add_argument("--run-id", default="")
    tw.add_argument("--project-id", default="")
    tw.add_argument("--timeout", type=int, default=3600)
    tw.add_argument("--json", action="store_true")
    tw.set_defaults(fn=cmd_team_watch)

    tp = tm_sp.add_parser("proposals", help="List team proposals managed by the runtime")
    tp.add_argument("--team-id", required=True)
    tp.add_argument("--target-id", default="", help="filter by improvement target id")
    tp.add_argument("--project-id", default="", help="filter by project id")
    tp.add_argument("--lane", default="", help="filter by lane")
    tp.add_argument("--status", default="", help="filter by proposal status")
    tp.add_argument("--json", action="store_true", help="print raw JSON")
    tp.set_defaults(fn=cmd_team_proposals)

    td = tm_sp.add_parser("decide", help="Approve, reject, or hold a team proposal")
    td.add_argument("--team-id", required=True)
    td.add_argument("proposal_id")
    td.add_argument("action", choices=["approve", "reject", "hold"])
    td.add_argument("--title", default="")
    td.add_argument("--summary", default="")
    td.add_argument("--version-bump", default="")
    td.add_argument("--json", action="store_true")
    td.set_defaults(fn=cmd_team_decide)

    tsy = tm_sp.add_parser("discussions-sync", help="Poll and reconcile team proposal discussions")
    tsy.add_argument("--team-id", required=True)
    tsy.add_argument("--json", action="store_true")
    tsy.set_defaults(fn=cmd_team_discussions_sync)

    tc = tm_sp.add_parser("coding", help="Team coding/delivery operations")
    tc_sp = tc.add_subparsers(dest="team_coding_cmd", required=True)

    tcr = tc_sp.add_parser("run", help="Run team coding workers")
    tcr.add_argument("--team-id", required=True)
    tcr.add_argument("--project")
    tcr.add_argument("--target-id", default="")
    tcr.add_argument("--task-id", default="")
    tcr.add_argument("--dry-run", action="store_true")
    tcr.add_argument("--force", action="store_true")
    tcr.add_argument("--concurrency", type=int, default=10)
    tcr.add_argument("--json", action="store_true")
    tcr.set_defaults(fn=cmd_team_coding_run)

    tct = tc_sp.add_parser("tasks", help="List team coding tasks")
    tct.add_argument("--team-id", required=True)
    tct.add_argument("--project")
    tct.add_argument("--target-id", default="")
    tct.add_argument("--status", default="")
    tct.add_argument("--json", action="store_true")
    tct.set_defaults(fn=cmd_team_coding_tasks)

    tlg = tm_sp.add_parser("logs", help="Show team run logs")
    tlg.add_argument("--team-id", required=True)
    tlg.add_argument("--run-id", default="")
    tlg.add_argument("--limit", type=int, default=200)
    tlg.add_argument("--json", action="store_true")
    tlg.set_defaults(fn=cmd_team_logs)

    tbl = tm_sp.add_parser("bug-scan-live", help="Run a live whole-repository bug scan for a team")
    tbl.add_argument("--team-id", required=True)
    tbl.add_argument("--target-id", required=True)
    tbl.add_argument("--project-id", default="")
    tbl.add_argument("--container", default="")
    tbl.add_argument("--json", action="store_true")
    tbl.set_defaults(fn=cmd_team_bug_scan_live)

    tgt = sp.add_parser("improvement-targets", help="List registered improvement targets")
    tgt.add_argument("--project-id", default="", help="filter by project id")
    tgt.add_argument("--enabled-only", action="store_true", help="show enabled targets only")
    tgt.add_argument("--json", action="store_true", help="print raw JSON")
    tgt.set_defaults(fn=cmd_improvement_targets)

    tga = sp.add_parser("improvement-target-add", help="Register or update an improvement target")
    tga.add_argument("--target-id", default="", help="explicit target id")
    tga.add_argument("--display-name", default="", help="human readable target name")
    tga.add_argument("--repo-path", default="", help="local repository path")
    tga.add_argument("--repo-url", default="", help="remote repository URL")
    tga.add_argument("--repo-locator", default="", help="GitHub repo locator owner/name")
    tga.add_argument("--default-branch", default="", help="default branch override")
    tga.add_argument("--disable", action="store_true", help="register target but mark disabled")
    tga.add_argument("--auto-discovery", action="store_true", help="deprecated compatibility flag; automatic discovery is now controlled globally and by workflow settings")
    tga.add_argument("--auto-delivery", action="store_true", help="deprecated compatibility flag; automatic delivery is now controlled globally and by workflow settings")
    tga.add_argument("--ship-enabled", action="store_true", help="allow release/ship actions for this target")
    tga.add_argument("--workstream", default="general", help="default workstream id")
    tga.add_argument("--json", action="store_true", help="print raw JSON")
    tga.set_defaults(fn=cmd_improvement_target_add)

    ocs = sp.add_parser("openclaw-status", help="Show OpenTeam OpenClaw reporting status and detection")
    ocs.add_argument("--json", action="store_true", help="print raw JSON")
    ocs.set_defaults(fn=cmd_openclaw_status)

    occ = sp.add_parser("openclaw-config", help="Configure OpenTeam OpenClaw reporting route")
    occ.add_argument("--enable", action="store_true", help="enable OpenClaw reporting")
    occ.add_argument("--disable", action="store_true", help="disable OpenClaw reporting")
    occ.add_argument("--channel", default="", help="channel id, for example telegram")
    occ.add_argument("--target", default="", help="target id, for example a Telegram chat id or @channel")
    occ.add_argument("--gateway-mode", default="", help="gateway mode override, usually remote")
    occ.add_argument("--gateway-url", default="", help="gateway websocket url, for example ws://host.docker.internal:18789")
    occ.add_argument("--gateway-token", default="", help="gateway token override")
    occ.add_argument("--gateway-password", default="", help="gateway password override")
    occ.add_argument("--gateway-transport", default="", help="gateway transport, for example direct")
    occ.add_argument("--gateway-state-dir", default="", help="persistent OpenClaw client state dir")
    occ.add_argument("--allow-insecure-private-ws", action="store_true", help="allow ws:// to a private non-loopback gateway")
    occ.add_argument("--disallow-insecure-private-ws", action="store_true", help="forbid ws:// to a private non-loopback gateway")
    occ.add_argument("--path", action="append", default=[], help="repo path glob to report, repeatable; use * for all")
    occ.add_argument("--event-type", action="append", default=[], help="event type glob to report, repeatable")
    occ.add_argument("--exclude-event-type", action="append", default=[], help="event type glob to exclude, repeatable")
    occ.add_argument("--message-prefix", default="", help="message title prefix")
    occ.add_argument("--json", action="store_true", help="print raw JSON")
    occ.set_defaults(fn=cmd_openclaw_config)

    oct = sp.add_parser("openclaw-test", help="Send a test report through OpenClaw")
    oct.add_argument("--message", default="OpenTeam OpenClaw test message", help="message body")
    oct.add_argument("--channel", default="", help="override configured channel")
    oct.add_argument("--target", default="", help="override configured target")
    oct.add_argument("--path", default="", help="optional repo path for route matching context")
    oct.add_argument("--dry-run", action="store_true", help="render and validate without sending")
    oct.add_argument("--json", action="store_true", help="print raw JSON")
    oct.set_defaults(fn=cmd_openclaw_test)

    ocsw = sp.add_parser("openclaw-sweep", help="Process queued runtime events through OpenClaw once")
    ocsw.add_argument("--dry-run", action="store_true", help="match and render without sending")
    ocsw.add_argument("--limit", type=int, default=100, help="max events to process in one sweep")
    ocsw.add_argument("--json", action="store_true", help="print raw JSON")
    ocsw.set_defaults(fn=cmd_openclaw_sweep)

    args = p.parse_args(argv)
    try:
        args.fn(args)
        return 0
    except Exception as e:
        eprint(f"ERROR: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
