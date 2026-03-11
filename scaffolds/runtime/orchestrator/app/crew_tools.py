from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional


class CrewToolsError(ValueError):
    pass


_FLOW_ALIASES: dict[str, str] = {
    "self_improve": "repo_improvement",
    "self_upgrade": "repo_improvement",
}

_NATIVE_CREWAI_FLOWS = frozenset({"repo_improvement"})

# CrewAI flow aliases route to supported runtime pipelines only.
_FLOW_PIPELINES: dict[str, list[str]] = {
    "genesis": ["doctor"],
    "standard": ["doctor"],
    "maintenance": ["doctor", "db_migrate"],
    "migration": ["db_migrate"],
}

# Backward-compatible direct pipeline mode is intentionally narrow.
_RUN_DIRECT_PIPELINE_ALLOWLIST = frozenset({"doctor", "db_migrate"})

_PIPELINE_SCRIPTS: dict[str, str] = {
    "doctor": "doctor.py",
    "db_migrate": "db_migrate.py",
    "task_create": "task_create.py",
}


def workspace_root() -> Path:
    env_ws = str((os.getenv("TEAMOS_WORKSPACE_ROOT") or "")).strip()
    if env_ws:
        return Path(env_ws).expanduser().resolve()
    return (Path.home() / ".teamos" / "workspace").resolve()


def normalize_flow(raw: Optional[str]) -> str:
    flow = str(raw or "standard").strip().lower()
    return _FLOW_ALIASES.get(flow, flow)


def supported_flows() -> list[str]:
    return sorted(set(_FLOW_PIPELINES.keys()) | set(_NATIVE_CREWAI_FLOWS))


def direct_pipeline_allowlist() -> list[str]:
    return sorted(_RUN_DIRECT_PIPELINE_ALLOWLIST)


def native_crewai_flows() -> list[str]:
    return sorted(_NATIVE_CREWAI_FLOWS)


def is_native_crewai_flow(flow: str) -> bool:
    return normalize_flow(flow) in _NATIVE_CREWAI_FLOWS


def resolve_run_request_flow(*, flow: Optional[str], pipeline: Optional[str]) -> str:
    """
    API compatibility:
    - preferred: flow=<alias>
    - legacy: pipeline=<name>  -> flow=pipeline:<name>
    """
    preferred = str(flow or "").strip()
    if preferred:
        return preferred
    direct = str(pipeline or "").strip()
    if direct:
        return f"pipeline:{direct}"
    return "standard"


def flow_to_pipelines(flow: str) -> list[str]:
    f = normalize_flow(flow)
    if f in _NATIVE_CREWAI_FLOWS:
        raise CrewToolsError(f"native_crewai_flow_has_no_pipeline_mapping: {f}")
    if f in _FLOW_PIPELINES:
        return list(_FLOW_PIPELINES[f])

    if f.startswith("pipeline:"):
        pipeline = f.split(":", 1)[1].strip()
        if pipeline in _RUN_DIRECT_PIPELINE_ALLOWLIST:
            return [pipeline]
        raise CrewToolsError(
            f"unsupported_direct_pipeline: {pipeline or '(empty)'}; "
            f"direct_pipeline_allowlist={direct_pipeline_allowlist()}"
        )

    if f in _RUN_DIRECT_PIPELINE_ALLOWLIST:
        return [f]

    raise CrewToolsError(
        f"unsupported_flow: {flow}; supported_flows={supported_flows()}; "
        f"direct_pipeline_allowlist={direct_pipeline_allowlist()}"
    )


def _pipeline_script(*, pipeline: str, repo_root: Path) -> Path:
    name = _PIPELINE_SCRIPTS.get(str(pipeline).strip())
    if not name:
        raise CrewToolsError(f"unsupported_pipeline: {pipeline}")
    return repo_root / "scripts" / "pipelines" / name


def pipeline_command(*, pipeline: str, repo_root: Path, workspace_root: Path, extra_args: Optional[Iterable[str]] = None) -> list[str]:
    script = _pipeline_script(pipeline=pipeline, repo_root=repo_root)
    cmd = [
        sys.executable,
        str(script),
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(workspace_root),
    ]
    if extra_args:
        cmd.extend(str(x) for x in extra_args)
    return cmd


def pipeline_write_evidence(*, pipeline: str, repo_root: Path) -> dict[str, Any]:
    script = _pipeline_script(pipeline=pipeline, repo_root=repo_root)
    return {
        "write_mode": "delegated_pipeline_script",
        "writer": "deterministic_pipeline_script",
        "pipeline": pipeline,
        "script_path": str(script),
        "agent_truth_source_write": "disabled",
    }


def run_write_evidence(*, pipelines: list[str], repo_root: Path) -> dict[str, Any]:
    scripts = [pipeline_write_evidence(pipeline=p, repo_root=repo_root) for p in pipelines]
    return {
        "write_mode": "delegated_pipeline_scripts",
        "writer": "deterministic_pipeline_scripts",
        "agent_truth_source_write": "disabled",
        "pipelines": scripts,
    }


def run_pipeline(*, pipeline: str, repo_root: Path, workspace_root: Path, extra_args: Optional[Iterable[str]] = None) -> dict[str, Any]:
    cmd = pipeline_command(pipeline=pipeline, repo_root=repo_root, workspace_root=workspace_root, extra_args=extra_args)
    script = _pipeline_script(pipeline=pipeline, repo_root=repo_root)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    return {
        "pipeline": pipeline,
        "script_path": str(script),
        "command": cmd,
        "returncode": int(p.returncode),
        "stdout": p.stdout or "",
        "stderr": p.stderr or "",
        "write_delegate": pipeline_write_evidence(pipeline=pipeline, repo_root=repo_root),
    }


def _parse_json_output(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        import json

        return json.loads(text)
    except Exception:
        return None


def run_task_create_pipeline(
    *,
    repo_root: Path,
    workspace_root: Path,
    scope: str,
    title: str,
    workstreams: list[str],
    mode: str,
    dry_run: bool,
) -> dict[str, Any]:
    ws = [str(x).strip() for x in (workstreams or []) if str(x).strip()]
    ws_arg = ",".join(ws) if ws else "general"
    extra = [
        "--scope",
        str(scope),
        "--title",
        str(title),
        "--workstreams",
        ws_arg,
        "--mode",
        str(mode or "auto"),
    ]
    if dry_run:
        extra.append("--dry-run")

    step = run_pipeline(
        pipeline="task_create",
        repo_root=repo_root,
        workspace_root=workspace_root,
        extra_args=extra,
    )
    parsed = _parse_json_output(step.get("stdout", ""))
    if step["returncode"] != 0:
        detail = ""
        if isinstance(parsed, dict):
            detail = str(parsed.get("error") or parsed.get("message") or "")
        if not detail:
            detail = (str(step.get("stderr") or "") or str(step.get("stdout") or "")).strip()[-300:]
        raise CrewToolsError(f"task_create_pipeline_failed rc={step['returncode']} detail={detail}")
    if not isinstance(parsed, dict):
        raise CrewToolsError("task_create_pipeline_invalid_output: expected JSON object")
    return {"result": parsed, "step": step}
