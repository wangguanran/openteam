"""Shared constants, config helpers, and utility functions for the Team OS CLI."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib as tomli  # type: ignore
except Exception:  # pragma: no cover
    try:
        import tomli  # type: ignore
    except Exception:  # pragma: no cover
        tomli = None


CONFIG_DIR = Path.home() / ".teamos"
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_WORKSPACE_ROOT = Path.home() / ".teamos" / "workspace"

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)


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


def _looks_like_teamos_repo(root: Path) -> bool:
    return (root / "scripts" / "pipelines").exists() and ((root / "teamos").exists() or (root / "TEAMOS.md").exists())


def _find_team_os_repo_root() -> Optional[Path]:
    """
    Best-effort Team OS repo root detection.
    Priority:
    1) env TEAM_OS_REPO_PATH
    2) this script location or parents containing AGENTS + scripts/pipelines
    3) current directory or parents containing AGENTS + scripts/pipelines
    """
    env = (os.getenv("TEAM_OS_REPO_PATH") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if _looks_like_teamos_repo(p):
            return p

    # When invoked by absolute path from another repo, detect relative to this file.
    try:
        here = Path(__file__).resolve()
        for p in [here.parent] + list(here.parents):
            if _looks_like_teamos_repo(p):
                return p
    except Exception:
        pass

    cur = Path.cwd().resolve()
    for p in [cur] + list(cur.parents):
        if _looks_like_teamos_repo(p):
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
    1) `TEAMOS_TASK_ID` env (supports branchless workflows)
    2) git branch name patterns (legacy)
    """
    env_tid = str(os.getenv("TEAMOS_TASK_ID") or "").strip()
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
        m = re.match(r"^teamos/((?:TEAMOS-[0-9]{4})|(?:TASK-[0-9]{8}-[0-9]{6}))-", b)
        return m.group(1) if m else ""
    except Exception:
        return ""


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
            raise RuntimeError(f"Unknown profile: {name}. Use: teamos config show")
        return {"name": name, **profiles[name]}
    cur = cfg.get("current_profile")
    if cur and cur in profiles:
        return {"name": cur, **profiles[cur]}
    if "local" in profiles:
        return {"name": "local", **profiles["local"]}
    raise RuntimeError("No profile configured. Run: teamos config init")


def _base_url(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    cfg = _load_config()
    prof = _get_profile(cfg, args.profile)
    base = str(prof.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("profile.base_url is empty; run: teamos config show")
    return base, prof


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
        v = str(os.getenv("TEAMOS_WORKSPACE_ROOT") or "").strip()
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
                    "# Team OS Workspace config (local; not committed)",
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


def _runtime_root_for_repo(repo_root: Path) -> Path:
    raw = str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    home = str(os.getenv("TEAMOS_HOME") or "").strip()
    if home:
        return (Path(home).expanduser().resolve() / "runtime" / "default").resolve()
    return (Path.home() / ".teamos" / "runtime" / "default").resolve()


def _default_project_id(prof: dict[str, Any], args: argparse.Namespace) -> str:
    return getattr(args, "project", "") or (prof.get("default_project_id") or "teamos")


def _default_scope(prof: dict[str, Any], args: argparse.Namespace) -> str:
    s = _norm(getattr(args, "scope", "") or "")
    if s:
        return s
    pid = _default_project_id(prof, args)
    return "teamos" if pid == "teamos" else f"project:{pid}"


def _inject_project_agents_manual(args: argparse.Namespace, *, project_id: str, repo_path: Optional[str] = None, reason: str = "") -> None:
    """
    Best-effort: ensure project repo root AGENTS.md contains Team-OS manual block.
    Non-leader runs are plan-only (leader-only enforced by pipeline).
    """
    repo_root = _find_team_os_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find Team OS repo root. Set env TEAM_OS_REPO_PATH or run from within the team-os repo.")

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


def shutil_which(cmd: str) -> Optional[str]:
    import shutil

    return shutil.which(cmd)
