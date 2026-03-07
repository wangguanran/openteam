from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from . import crewai_runtime
from . import crewai_self_upgrade as planning
from . import workspace_store
from .github_issues_bus import GitHubAuthError, GitHubIssuesBusError, update_issue


class DeliveryError(RuntimeError):
    pass


class DeliveryImplementationResult(BaseModel):
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    tests_to_run: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class DeliveryReviewResult(BaseModel):
    approved: bool = False
    summary: str = ""
    feedback: list[str] = Field(default_factory=list)


class DeliveryQAResult(BaseModel):
    approved: bool = False
    summary: str = ""
    commands: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)


_SAFE_TEST_PREFIXES = (
    "python -m unittest",
    "python3 -m unittest",
    "python -m pytest",
    "python3 -m pytest",
    "pytest",
    "uv run pytest",
    "uv run python -m unittest",
    "npm test",
    "npm run test",
    "pnpm test",
    "pnpm run test",
    "yarn test",
    "go test",
    "cargo test",
)


def _utc_now_iso() -> str:
    return planning._utc_now_iso()


def _slug(text: str, *, default: str = "item") -> str:
    return planning._slug(text, default=default)


def _env_truthy(name: str, default: str = "0") -> bool:
    return planning._env_truthy(name, default)


def _runtime_root() -> Path:
    return planning._runtime_root()


def _worktrees_root() -> Path:
    return planning._worktrees_root()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _task_scope(project_id: str) -> str:
    return "teamos" if str(project_id or "").strip() == "teamos" else f"project:{str(project_id or '').strip()}"


def _task_ledger_dir(project_id: str) -> Path:
    if str(project_id or "").strip() == "teamos":
        return planning.ledger_tasks_dir()
    workspace_store.ensure_project_scaffold(project_id)
    return workspace_store.ledger_tasks_dir(project_id)


def _logs_dir_for_doc(doc: dict[str, Any], *, ledger_path: Path, source_repo_root: Path) -> Path:
    artifacts = doc.get("artifacts") or {}
    raw = str((artifacts if isinstance(artifacts, dict) else {}).get("logs_dir") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p.resolve()
        return (source_repo_root / raw).resolve()
    task_id = str(doc.get("id") or ledger_path.stem).strip()
    return (ledger_path.parent.parent.parent / "logs" / "tasks" / task_id).resolve()


def _is_self_upgrade_task(doc: dict[str, Any]) -> bool:
    orchestration = doc.get("orchestration") or {}
    if not isinstance(orchestration, dict):
        return False
    return (
        str(orchestration.get("engine") or "").strip().lower() == "crewai"
        and str(orchestration.get("flow") or "").strip().lower() == "self_upgrade"
    )


def _current_status(doc: dict[str, Any]) -> str:
    return str(doc.get("status") or doc.get("state") or "").strip().lower()


def _source_repo_root(doc: dict[str, Any]) -> Path:
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    exec_state = doc.get("self_upgrade_execution") or {}
    if not isinstance(exec_state, dict):
        exec_state = {}
    for raw in (
        exec_state.get("source_repo_root"),
        repo.get("source_workdir"),
        repo.get("workdir"),
    ):
        s = str(raw or "").strip()
        if s:
            return Path(s).expanduser().resolve()
    raise DeliveryError(f"task {doc.get('id') or '(unknown)'} missing repo.workdir")


def _worktree_repo_root(doc: dict[str, Any]) -> Optional[Path]:
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        return None
    s = str(repo.get("workdir") or "").strip()
    if not s:
        return None
    p = Path(s).expanduser().resolve()
    return p if p.exists() else None


def _execution_state(doc: dict[str, Any]) -> dict[str, Any]:
    raw = doc.get("self_upgrade_execution")
    return dict(raw) if isinstance(raw, dict) else {}


def _task_lane(doc: dict[str, Any]) -> str:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        su = {}
    lane = str(su.get("lane") or "").strip().lower()
    return lane or "bug"


def _task_work_item(doc: dict[str, Any]) -> dict[str, Any]:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        return {}
    work_item = su.get("work_item") or {}
    return dict(work_item) if isinstance(work_item, dict) else {}


def _task_worktree_title(doc: dict[str, Any], task_id: str) -> str:
    work_item = _task_work_item(doc)
    return str(work_item.get("title") or doc.get("title") or task_id).strip() or task_id


def _normalized_task_worktree_root(doc: dict[str, Any], *, task_id: str, source_repo_root: Path, raw_hint: str) -> Path:
    return Path(
        planning._normalize_worktree_hint(
            repo_root=source_repo_root,
            lane=_task_lane(doc),
            title=_task_worktree_title(doc, task_id),
            raw_hint=str(raw_hint or "").strip(),
        )
    ).resolve()


def _absolute_path(raw: Any) -> Optional[Path]:
    s = str(raw or "").strip()
    if not s:
        return None
    p = Path(s).expanduser()
    if not p.is_absolute():
        return None
    return p.resolve()


def _prune_empty_parents(start: Path, *, stop_at: Path) -> None:
    current = start.resolve()
    limit = stop_at.resolve()
    while current != limit and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _move_worktree_root(*, source_repo_root: Path, legacy_root: Path, target_root: Path) -> Path:
    if legacy_root.resolve() == target_root.resolve():
        return target_root
    target_root.parent.mkdir(parents=True, exist_ok=True)
    if target_root.exists():
        if any(target_root.iterdir()):
            raise DeliveryError(f"worktree target exists and is not empty: {target_root}")
        target_root.rmdir()
    if _is_git_checkout(legacy_root):
        out = _run(
            ["git", "-C", str(source_repo_root), "worktree", "move", str(legacy_root), str(target_root)],
            cwd=source_repo_root,
            timeout_sec=180,
        )
        if int(out.get("returncode", 1)) != 0:
            detail = str(out.get("stderr") or out.get("stdout") or "").strip()[:500]
            raise DeliveryError(f"git worktree move failed: {detail}")
    else:
        shutil.move(str(legacy_root), str(target_root))
    _prune_empty_parents(legacy_root.parent, stop_at=source_repo_root)
    return target_root


def _allowed_paths(doc: dict[str, Any]) -> list[str]:
    execution_policy = doc.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        execution_policy = {}
    raw = execution_policy.get("allowed_paths") or []
    return [str(x).strip().strip("/") for x in raw if str(x).strip()]


def _tests_allowlist(doc: dict[str, Any]) -> list[str]:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        su = {}
    work_item = su.get("work_item") or {}
    if not isinstance(work_item, dict):
        work_item = {}
    raw = work_item.get("tests") or su.get("tests") or []
    return [str(x).strip() for x in raw if str(x).strip()]


def _acceptance_items(doc: dict[str, Any]) -> list[str]:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        su = {}
    work_item = su.get("work_item") or {}
    if not isinstance(work_item, dict):
        work_item = {}
    raw = work_item.get("acceptance") or su.get("acceptance") or []
    return [str(x).strip() for x in raw if str(x).strip()]


def _issue_url(doc: dict[str, Any]) -> str:
    links = doc.get("links") or {}
    if not isinstance(links, dict):
        return ""
    return str(links.get("issue") or "").strip()


def _append_markdown(logs_dir: Path, filename: str, heading: str, lines: list[str]) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / filename
    with path.open("a", encoding="utf-8") as f:
        f.write("\n")
        f.write(f"## {heading} ({_utc_now_iso()})\n\n")
        for line in lines:
            text = str(line or "").rstrip()
            if not text:
                f.write("\n")
            elif text.startswith("- ") or text.startswith("1. "):
                f.write(text + "\n")
            else:
                f.write(f"- {text}\n")
        f.write("\n")


def _append_metric(logs_dir: Path, *, event_type: str, actor: str, task_id: str, project_id: str, workstream_id: str, message: str, payload: dict[str, Any], severity: str = "INFO") -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "metrics.jsonl"
    item = {
        "ts": _utc_now_iso(),
        "event_type": event_type,
        "actor": actor,
        "task_id": task_id,
        "project_id": project_id,
        "workstream_id": workstream_id,
        "severity": severity,
        "message": message,
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _run(cmd: list[str], *, cwd: Path, timeout_sec: int = 300) -> dict[str, Any]:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout_sec,
    )
    return {
        "command": cmd,
        "returncode": int(p.returncode),
        "stdout": str(p.stdout or ""),
        "stderr": str(p.stderr or ""),
    }


def _is_git_checkout(path: Path) -> bool:
    dotgit = path / ".git"
    return dotgit.exists()


def _normalize_repo_relative_path(path: str) -> str:
    rel = str(path or "").strip().replace("\\", "/").lstrip("/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _resolve_repo_path(repo_root: Path, rel_path: str) -> Path:
    rel = _normalize_repo_relative_path(rel_path)
    if not rel or rel == ".":
        return repo_root.resolve()
    candidate = (repo_root / rel).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except Exception as exc:
        raise DeliveryError(f"path escapes repo root: {rel}") from exc
    return candidate


def _is_allowed_path(rel_path: str, allowed_paths: list[str]) -> bool:
    rel = _normalize_repo_relative_path(rel_path)
    if not rel:
        return False
    if not allowed_paths:
        return False
    for allowed in allowed_paths:
        base = _normalize_repo_relative_path(allowed)
        if not base:
            continue
        if rel == base or rel.startswith(base.rstrip("/") + "/"):
            return True
    return False


def _git_status_text(repo_root: Path, *, max_chars: int = 4000) -> str:
    out = _run(["git", "-C", str(repo_root), "status", "--short"], cwd=repo_root, timeout_sec=60)
    text = (out.get("stdout") or out.get("stderr") or "").strip()
    return str(text)[:max_chars]


def _changed_files(repo_root: Path) -> list[str]:
    out = _run(["git", "-C", str(repo_root), "status", "--porcelain"], cwd=repo_root, timeout_sec=60)
    rows = []
    for raw in str(out.get("stdout") or "").splitlines():
        line = raw.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        rows.append(_normalize_repo_relative_path(path.split(" -> ")[-1]))
    return [x for x in rows if x]


def _git_diff_text(repo_root: Path, *, allowed_paths: list[str], max_chars: int = 12000) -> str:
    cmd = ["git", "-C", str(repo_root), "diff", "--"]
    cmd.extend(allowed_paths or ["."])
    out = _run(cmd, cwd=repo_root, timeout_sec=90)
    text = str(out.get("stdout") or out.get("stderr") or "")
    return text[:max_chars]


def _safe_test_command(command: str, *, allowlist: list[str]) -> bool:
    cmd = str(command or "").strip()
    if not cmd:
        return False
    if allowlist and cmd in allowlist:
        return True
    return any(cmd.startswith(prefix) for prefix in _SAFE_TEST_PREFIXES)


def _close_issue_if_possible(doc: dict[str, Any]) -> str:
    issue_url = _issue_url(doc)
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    repo_locator = str(repo.get("locator") or "").strip()
    if not issue_url or not repo_locator:
        return ""
    m = re.search(r"/issues/(\d+)(?:$|[?#])", issue_url)
    if not m:
        return ""
    try:
        issue_number = int(m.group(1))
    except Exception:
        return ""
    try:
        issue = update_issue(repo_locator, issue_number, state="closed")
        return str(issue.url or issue_url)
    except (GitHubAuthError, GitHubIssuesBusError):
        return issue_url


def _update_task_state(
    ledger_path: Path,
    doc: dict[str, Any],
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    owner_role: str = "",
    extra_execution: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    if status:
        doc["status"] = status
    checkpoint = doc.get("checkpoint") or {}
    if not isinstance(checkpoint, dict):
        checkpoint = {}
    if stage:
        checkpoint["stage"] = stage
    checkpoint["last_event_ts"] = now
    doc["checkpoint"] = checkpoint
    execution = _execution_state(doc)
    if stage:
        execution["stage"] = stage
    execution["last_run_at"] = now
    history = list(execution.get("history") or [])
    if stage:
        history.append({"ts": now, "stage": stage, "status": str(status or doc.get('status') or "")})
    execution["history"] = history[-50:]
    if isinstance(extra_execution, dict):
        execution.update(extra_execution)
    doc["self_upgrade_execution"] = execution
    doc["updated_at"] = now
    if owner_role:
        doc["owners"] = [owner_role]
        doc["owner_role"] = owner_role
        roles = []
        for raw in list(doc.get("roles_involved") or []):
            val = str(raw or "").strip()
            if val and val not in roles:
                roles.append(val)
        if owner_role not in roles:
            roles.append(owner_role)
        doc["roles_involved"] = roles
    _write_yaml(ledger_path, doc)
    return doc


def _read_repo_path_blob(repo_root: Path, rel: str, *, limit: int = 12000) -> str:
    path = (repo_root / rel).resolve()
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _ensure_task_worktree(ledger_path: Path, doc: dict[str, Any]) -> tuple[dict[str, Any], Path, Path]:
    task_id = str(doc.get("id") or ledger_path.stem).strip()
    source_repo_root = _source_repo_root(doc)
    if not source_repo_root.exists():
        raise DeliveryError(f"source repo does not exist: {source_repo_root}")

    execution = _execution_state(doc)
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    execution_policy = doc.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        execution_policy = {}
    desired_raw = str(execution.get("worktree_path") or execution_policy.get("worktree_hint") or "").strip()
    worktree_root = _normalized_task_worktree_root(
        doc,
        task_id=task_id,
        source_repo_root=source_repo_root,
        raw_hint=desired_raw,
    )
    branch_name = str(execution.get("branch_name") or f"codex/self-upgrade/{_slug(task_id.lower(), default='task')}").strip()
    base_branch_out = _run(["git", "-C", str(source_repo_root), "rev-parse", "--abbrev-ref", "HEAD"], cwd=source_repo_root, timeout_sec=30)
    base_branch = str(base_branch_out.get("stdout") or "").strip() or "main"
    legacy_roots: list[Path] = []
    for raw in (
        execution.get("worktree_path"),
        execution_policy.get("worktree_hint"),
        repo.get("workdir"),
    ):
        candidate = _absolute_path(raw)
        if candidate is None:
            continue
        if candidate == source_repo_root or candidate == worktree_root:
            continue
        if candidate not in legacy_roots:
            legacy_roots.append(candidate)

    if _is_git_checkout(worktree_root):
        repo["workdir"] = str(worktree_root)
    else:
        legacy_root = next((candidate for candidate in legacy_roots if candidate.exists()), None)
        if legacy_root is not None:
            worktree_root = _move_worktree_root(source_repo_root=source_repo_root, legacy_root=legacy_root, target_root=worktree_root)
            repo["workdir"] = str(worktree_root)
        else:
            if worktree_root.exists() and any(worktree_root.iterdir()):
                raise DeliveryError(f"worktree target exists and is not empty: {worktree_root}")
            worktree_root.parent.mkdir(parents=True, exist_ok=True)
            out = _run(
                ["git", "-C", str(source_repo_root), "worktree", "add", "-B", branch_name, str(worktree_root), "HEAD"],
                cwd=source_repo_root,
                timeout_sec=120,
            )
            if int(out.get("returncode", 1)) != 0:
                detail = str(out.get("stderr") or out.get("stdout") or "").strip()[:500]
                raise DeliveryError(f"git worktree add failed: {detail}")
            execution["base_branch"] = str(execution.get("base_branch") or base_branch)
            repo["workdir"] = str(worktree_root)

    repo["source_workdir"] = str(source_repo_root)
    repo["branch"] = branch_name
    doc["repo"] = repo
    work_item = _task_work_item(doc)
    if work_item:
        work_item["worktree_hint"] = str(worktree_root)
        su = doc.get("self_upgrade") or {}
        if isinstance(su, dict):
            su["work_item"] = work_item
            doc["self_upgrade"] = su
    execution_policy["worktree_hint"] = str(worktree_root)
    doc["execution_policy"] = execution_policy
    execution.update(
        {
            "worktree_path": str(worktree_root),
            "branch_name": branch_name,
            "source_repo_root": str(source_repo_root),
            "base_branch": str(execution.get("base_branch") or base_branch or "main"),
            "worktree_ready_at": str(execution.get("worktree_ready_at") or _utc_now_iso()),
        }
    )
    doc["self_upgrade_execution"] = execution
    _write_yaml(ledger_path, doc)
    return doc, worktree_root, source_repo_root


def _build_repo_tools(*, repo_root: Path, allowed_paths: list[str], tests_allowlist: list[str]):
    from crewai.tools import tool

    @tool("List Allowed Paths")
    def list_allowed_paths() -> str:
        """Return the repository paths this task is allowed to modify."""
        if not allowed_paths:
            return "No writable paths were provided for this task."
        return "\n".join(f"- {p}" for p in allowed_paths)

    @tool("List Directory")
    def list_directory(relative_path: str = ".") -> str:
        """List files under a repository directory. Use paths relative to the repo root."""
        rel = _normalize_repo_relative_path(relative_path or ".") or "."
        try:
            base = _resolve_repo_path(repo_root, rel)
        except DeliveryError as e:
            return f"directory_blocked: {e}"
        if not base.exists() or not base.is_dir():
            return f"directory_not_found: {rel}"
        rows: list[str] = []
        for child in sorted(base.iterdir()):
            try:
                shown = str(child.relative_to(repo_root))
            except Exception:
                shown = child.name
            rows.append(shown + ("/" if child.is_dir() else ""))
            if len(rows) >= 200:
                break
        return "\n".join(rows) or "(empty)"

    @tool("Search Repository")
    def search_repository(pattern: str) -> str:
        """Search repository text and return matching lines. Use simple text or regex patterns."""
        pat = str(pattern or "").strip()
        if not pat:
            return "pattern is required"
        targets = allowed_paths or ["."]
        cmd = ["rg", "-n", "--hidden", "--glob", "!.git", pat, *targets]
        try:
            out = _run(cmd, cwd=repo_root, timeout_sec=30)
        except Exception:
            out = {"returncode": 1, "stdout": "", "stderr": "rg_failed"}
        text = str(out.get("stdout") or out.get("stderr") or "").strip()
        return text[:12000] or "(no matches)"

    @tool("Read Repository File")
    def read_repository_file(relative_path: str) -> str:
        """Read a UTF-8 repository file and return its contents. Use a path relative to the repo root."""
        rel = _normalize_repo_relative_path(relative_path)
        if not rel:
            return "relative_path is required"
        try:
            path = _resolve_repo_path(repo_root, rel)
        except DeliveryError as e:
            return f"read_blocked: {e}"
        if not path.exists() or not path.is_file():
            return f"file_not_found: {rel}"
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:20000]
        except Exception as e:
            return f"read_failed: {e}"

    @tool("Write Repository File")
    def write_repository_file(relative_path: str, content: str) -> str:
        """Write UTF-8 text to an allowed repository file. Only use allowed paths for this task."""
        rel = _normalize_repo_relative_path(relative_path)
        if not rel:
            return "relative_path is required"
        if not _is_allowed_path(rel, allowed_paths):
            return f"write_blocked_outside_allowed_paths: {rel}"
        try:
            path = _resolve_repo_path(repo_root, rel)
        except DeliveryError as e:
            return f"write_blocked: {e}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content or ""), encoding="utf-8")
        return f"wrote: {rel}"

    @tool("Git Status")
    def git_status() -> str:
        """Return the current git status for the task worktree."""
        return _git_status_text(repo_root)

    @tool("Git Diff")
    def git_diff() -> str:
        """Return the current git diff limited to this task's allowed paths."""
        return _git_diff_text(repo_root, allowed_paths=allowed_paths)

    @tool("Run Validation Command")
    def run_validation_command(command: str) -> str:
        """Run a safe validation command such as pytest or python -m unittest and return JSON output."""
        cmd = str(command or "").strip()
        if not _safe_test_command(cmd, allowlist=tests_allowlist):
            return json.dumps({"ok": False, "error": "command_not_allowed", "command": cmd}, ensure_ascii=False)
        try:
            parts = shlex.split(cmd)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"command_parse_failed: {e}", "command": cmd}, ensure_ascii=False)
        out = _run(parts, cwd=repo_root, timeout_sec=600)
        return json.dumps(
            {
                "ok": int(out.get("returncode", 1)) == 0,
                "command": cmd,
                "returncode": int(out.get("returncode", 1)),
                "stdout": str(out.get("stdout") or "")[-4000:],
                "stderr": str(out.get("stderr") or "")[-4000:],
            },
            ensure_ascii=False,
        )

    read_tools = [list_allowed_paths, list_directory, search_repository, read_repository_file, git_status, git_diff]
    write_tools = list(read_tools) + [write_repository_file, run_validation_command]
    qa_tools = [list_allowed_paths, read_repository_file, git_status, git_diff, run_validation_command]
    return {"read": read_tools, "write": write_tools, "qa": qa_tools}


def _coerce_model_output(raw_output: Any, model_cls: type[BaseModel], *, error_text: str) -> BaseModel:
    obj: Any = None
    if hasattr(raw_output, "to_dict"):
        try:
            obj = raw_output.to_dict()
        except Exception:
            obj = None
    if not obj and hasattr(raw_output, "json_dict"):
        obj = getattr(raw_output, "json_dict", None)
    if obj:
        return model_cls.model_validate(obj)
    text = str(raw_output or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise DeliveryError(error_text)
    return model_cls.model_validate(json.loads(match.group(0)))


def _kickoff_task_output(*, agent: Any, name: str, description: str, expected_output: str, model_cls: type[BaseModel], verbose: bool) -> BaseModel:
    from crewai import Crew, Process, Task

    task = Task(
        name=name,
        description=description,
        expected_output=expected_output,
        agent=agent,
        output_json=model_cls,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=verbose)
    out = crew.kickoff()
    return _coerce_model_output(out, model_cls, error_text=f"CrewAI returned no structured output for task={name}")


def _run_coding_stage(*, task_doc: dict[str, Any], worktree_root: Path, feedback: list[str], verbose: bool) -> DeliveryImplementationResult:
    crewai_runtime.require_crewai_importable()
    from crewai import Agent

    task_id = str(task_doc.get("id") or "").strip()
    allowed_paths = _allowed_paths(task_doc)
    tests_allowlist = _tests_allowlist(task_doc)
    acceptance = _acceptance_items(task_doc)
    repo = task_doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    tools = _build_repo_tools(repo_root=worktree_root, allowed_paths=allowed_paths, tests_allowlist=tests_allowlist)
    llm = planning._crewai_llm()
    blob = json.dumps(
        {
            "task_id": task_id,
            "title": task_doc.get("title") or "",
            "summary": ((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("summary") or "",
            "rationale": ((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("rationale") or "",
            "issue_url": _issue_url(task_doc),
            "repo_locator": repo.get("locator") or "",
            "allowed_paths": allowed_paths,
            "tests": tests_allowlist,
            "acceptance": acceptance,
            "feedback": feedback,
            "current_status": _git_status_text(worktree_root),
        },
        ensure_ascii=False,
        indent=2,
    )
    agent = Agent(
        role=str(task_doc.get("owner_role") or "Coding-Agent"),
        goal="Implement the approved self-upgrade task directly in the repository while staying inside the declared issue scope.",
        backstory="You are a disciplined software engineer. You only change allowed paths, you run validation before stopping, and you do not add unrelated improvements.",
        llm=llm,
        tools=tools["write"],
        allow_delegation=False,
        verbose=verbose,
    )
    out = _kickoff_task_output(
        agent=agent,
        name="implement_self_upgrade_task",
        description=(
            "Implement the task directly in the repository using the provided tools.\n"
            "Rules:\n"
            "- Modify only files under allowed_paths.\n"
            "- If allowed_paths is empty, report the blocker instead of editing random files.\n"
            "- Keep commits/task history issue-scoped.\n"
            "- Run relevant validation commands before you finish.\n"
            "- If a blocker remains, report it in unresolved.\n\n"
            f"Task context:\n{blob}"
        ),
        expected_output="A structured JSON summary of the implementation attempt.",
        model_cls=DeliveryImplementationResult,
        verbose=verbose,
    )
    return DeliveryImplementationResult.model_validate(out.model_dump())


def _run_review_stage(*, task_doc: dict[str, Any], worktree_root: Path, verbose: bool) -> DeliveryReviewResult:
    crewai_runtime.require_crewai_importable()
    from crewai import Agent

    allowed_paths = _allowed_paths(task_doc)
    tests_allowlist = _tests_allowlist(task_doc)
    tools = _build_repo_tools(repo_root=worktree_root, allowed_paths=allowed_paths, tests_allowlist=tests_allowlist)
    llm = planning._crewai_llm()
    blob = json.dumps(
        {
            "task_id": task_doc.get("id") or "",
            "title": task_doc.get("title") or "",
            "issue_url": _issue_url(task_doc),
            "allowed_paths": allowed_paths,
            "acceptance": _acceptance_items(task_doc),
            "git_status": _git_status_text(worktree_root),
        },
        ensure_ascii=False,
        indent=2,
    )
    agent = Agent(
        role=str((((task_doc.get("execution_policy") or {}) if isinstance(task_doc.get("execution_policy"), dict) else {}).get("review_role")) or "Review-Agent"),
        goal="Review the current task diff and reject anything outside scope, under-tested, or inconsistent with the task contract.",
        backstory="You are a strict code reviewer. You care about scope discipline, testability, and acceptance coverage.",
        llm=llm,
        tools=tools["read"],
        allow_delegation=False,
        verbose=verbose,
    )
    out = _kickoff_task_output(
        agent=agent,
        name="review_self_upgrade_task",
        description=(
            "Review the current repository diff for this self-upgrade task.\n"
            "Approve only when:\n"
            "- changed files stay inside allowed_paths\n"
            "- the change matches the task title/summary\n"
            "- validation/test coverage looks adequate\n"
            "- there are no obvious review blockers\n\n"
            f"Task context:\n{blob}"
        ),
        expected_output="A structured JSON review decision.",
        model_cls=DeliveryReviewResult,
        verbose=verbose,
    )
    return DeliveryReviewResult.model_validate(out.model_dump())


def _run_qa_stage(*, task_doc: dict[str, Any], worktree_root: Path, verbose: bool) -> DeliveryQAResult:
    crewai_runtime.require_crewai_importable()
    from crewai import Agent

    allowed_paths = _allowed_paths(task_doc)
    tests_allowlist = _tests_allowlist(task_doc)
    tools = _build_repo_tools(repo_root=worktree_root, allowed_paths=allowed_paths, tests_allowlist=tests_allowlist)
    llm = planning._crewai_llm()
    blob = json.dumps(
        {
            "task_id": task_doc.get("id") or "",
            "title": task_doc.get("title") or "",
            "tests": tests_allowlist,
            "acceptance": _acceptance_items(task_doc),
            "git_status": _git_status_text(worktree_root),
        },
        ensure_ascii=False,
        indent=2,
    )
    agent = Agent(
        role=str((((task_doc.get("execution_policy") or {}) if isinstance(task_doc.get("execution_policy"), dict) else {}).get("qa_role")) or "QA-Agent"),
        goal="Run the declared validation commands and confirm the task meets its acceptance criteria before release.",
        backstory="You are the QA gate. If tests fail or acceptance is weak, you block release and send the task back.",
        llm=llm,
        tools=tools["qa"],
        allow_delegation=False,
        verbose=verbose,
    )
    out = _kickoff_task_output(
        agent=agent,
        name="qa_self_upgrade_task",
        description=(
            "Act as the QA gate for this task.\n"
            "Use the validation tool to run the declared tests when needed.\n"
            "Approve only if the commands pass and the acceptance criteria are covered.\n\n"
            f"Task context:\n{blob}"
        ),
        expected_output="A structured JSON QA decision.",
        model_cls=DeliveryQAResult,
        verbose=verbose,
    )
    return DeliveryQAResult.model_validate(out.model_dump())


def _release_task(*, task_doc: dict[str, Any], ledger_path: Path, worktree_root: Path) -> dict[str, Any]:
    task_id = str(task_doc.get("id") or ledger_path.stem).strip()
    allowed_paths = _allowed_paths(task_doc)
    execution_policy = task_doc.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        execution_policy = {}
    execution = _execution_state(task_doc)
    commit_message = str(execution_policy.get("commit_message_template") or f"{task_id}: {task_doc.get('title') or 'self-upgrade task'}").strip()
    base_branch = str(execution.get("base_branch") or "main").strip() or "main"
    branch_name = str(execution.get("branch_name") or _run(["git", "-C", str(worktree_root), "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_root, timeout_sec=30).get("stdout") or "").strip()
    add_cmd = ["git", "-C", str(worktree_root), "add", "-A", "--"]
    add_cmd.extend(allowed_paths or ["."])
    add_out = _run(add_cmd, cwd=worktree_root, timeout_sec=60)
    if int(add_out.get("returncode", 1)) != 0:
        detail = str(add_out.get("stderr") or add_out.get("stdout") or "").strip()[:500]
        raise DeliveryError(f"git add failed: {detail}")
    staged = _run(["git", "-C", str(worktree_root), "diff", "--cached", "--name-only"], cwd=worktree_root, timeout_sec=30)
    staged_files = [str(x).strip() for x in str(staged.get("stdout") or "").splitlines() if str(x).strip()]
    if not staged_files:
        raise DeliveryError("release blocked: no staged changes for this task")
    commit_out = _run(["git", "-C", str(worktree_root), "commit", "-m", commit_message], cwd=worktree_root, timeout_sec=120)
    if int(commit_out.get("returncode", 1)) != 0:
        detail = str(commit_out.get("stderr") or commit_out.get("stdout") or "").strip()[:500]
        raise DeliveryError(f"git commit failed: {detail}")
    sha_out = _run(["git", "-C", str(worktree_root), "rev-parse", "HEAD"], cwd=worktree_root, timeout_sec=30)
    commit_sha = str(sha_out.get("stdout") or "").strip()
    origin_out = _run(["git", "-C", str(worktree_root), "remote", "get-url", "origin"], cwd=worktree_root, timeout_sec=30)
    origin = str(origin_out.get("stdout") or "").strip()
    if not origin:
        raise DeliveryError("release blocked: git remote 'origin' is missing")
    push_out = _run(["git", "-C", str(worktree_root), "push", "-u", "origin", branch_name], cwd=worktree_root, timeout_sec=180)
    if int(push_out.get("returncode", 1)) != 0:
        detail = str(push_out.get("stderr") or push_out.get("stdout") or "").strip()[:500]
        raise DeliveryError(f"git push failed: {detail}")
    pr_url = ""
    gh_auth = _run(["gh", "auth", "status", "-h", "github.com"], cwd=worktree_root, timeout_sec=30)
    if int(gh_auth.get("returncode", 1)) == 0 and branch_name and branch_name != base_branch:
        view_out = _run(["gh", "pr", "view", "--json", "url", "--jq", ".url"], cwd=worktree_root, timeout_sec=30)
        pr_url = str(view_out.get("stdout") or "").strip()
        if not pr_url:
            body = "\n".join(
                [
                    f"Task: {task_id}",
                    f"Issue: {_issue_url(task_doc) or '(none)'}",
                    "",
                    "Acceptance:",
                    *[f"- {item}" for item in _acceptance_items(task_doc)],
                ]
            )
            pr_out = _run(["gh", "pr", "create", "--title", commit_message, "--body", body, "--base", base_branch, "--head", branch_name], cwd=worktree_root, timeout_sec=120)
            if int(pr_out.get("returncode", 1)) == 0:
                pr_url = str(pr_out.get("stdout") or "").strip().splitlines()[-1].strip() if str(pr_out.get("stdout") or "").strip() else ""
    closed_issue_url = _close_issue_if_possible(task_doc)
    return {
        "branch": branch_name,
        "base_branch": base_branch,
        "commit_sha": commit_sha,
        "pull_request_url": pr_url,
        "issue_url": closed_issue_url or _issue_url(task_doc),
        "staged_files": staged_files,
    }


def _register_delivery_agents(*, db: Any, task_doc: dict[str, Any]) -> dict[str, str]:
    project_id = str(task_doc.get("project_id") or "teamos").strip() or "teamos"
    workstream_id = str(task_doc.get("workstream_id") or "general").strip() or "general"
    task_id = str(task_doc.get("id") or "").strip()
    execution_policy = task_doc.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        execution_policy = {}
    owner_role = str(task_doc.get("owner_role") or execution_policy.get("owner_role") or "Coding-Agent").strip() or "Coding-Agent"
    review_role = str(execution_policy.get("review_role") or "Review-Agent").strip() or "Review-Agent"
    qa_role = str(execution_policy.get("qa_role") or "QA-Agent").strip() or "QA-Agent"
    return {
        "Scheduler-Agent": db.register_agent(role_id="Scheduler-Agent", project_id=project_id, workstream_id=workstream_id, task_id=task_id, state="RUNNING", current_action="dispatching self-upgrade task"),
        owner_role: db.register_agent(role_id=owner_role, project_id=project_id, workstream_id=workstream_id, task_id=task_id, state="IDLE", current_action="waiting for coding"),
        review_role: db.register_agent(role_id=review_role, project_id=project_id, workstream_id=workstream_id, task_id=task_id, state="IDLE", current_action="waiting for review"),
        qa_role: db.register_agent(role_id=qa_role, project_id=project_id, workstream_id=workstream_id, task_id=task_id, state="IDLE", current_action="waiting for QA"),
        "Release-Agent": db.register_agent(role_id="Release-Agent", project_id=project_id, workstream_id=workstream_id, task_id=task_id, state="IDLE", current_action="waiting for release"),
        "Process-Metrics-Agent": db.register_agent(role_id="Process-Metrics-Agent", project_id=project_id, workstream_id=workstream_id, task_id=task_id, state="RUNNING", current_action="collecting delivery telemetry"),
    }


def _set_agent_state(db: Any, agent_ids: dict[str, str], role_id: str, *, state: str, action: str) -> None:
    agent_id = agent_ids.get(role_id)
    if not agent_id:
        return
    try:
        db.update_assignment(agent_id=agent_id, state=state, current_action=action)
    except Exception:
        pass


def _finish_delivery_agents(db: Any, agent_ids: dict[str, str], *, state: str, action: str) -> None:
    for agent_id in agent_ids.values():
        try:
            db.update_assignment(agent_id=agent_id, state=state, current_action=action)
        except Exception:
            pass


def _emit_event(db: Any, *, event_type: str, actor: str, task_doc: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        db.add_event(
            event_type=event_type,
            actor=actor,
            project_id=str(task_doc.get("project_id") or "teamos"),
            workstream_id=str(task_doc.get("workstream_id") or "general"),
            payload=payload,
        )
    except Exception:
        pass


def execute_task_delivery(*, db: Any, actor: str, ledger_path: Path, doc: dict[str, Any], dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    task_id = str(doc.get("id") or ledger_path.stem).strip()
    source_repo_root = _source_repo_root(doc)
    logs_dir = _logs_dir_for_doc(doc, ledger_path=ledger_path, source_repo_root=source_repo_root)
    status = _current_status(doc)
    if status in ("closed",) and not force:
        return {"ok": True, "task_id": task_id, "skipped": True, "reason": "task_already_closed"}
    if status == "blocked" and not force:
        return {"ok": True, "task_id": task_id, "skipped": True, "reason": "task_blocked"}

    project_id = str(doc.get("project_id") or "teamos").strip() or "teamos"
    workstream_id = str(doc.get("workstream_id") or "general").strip() or "general"
    owner_role = str(doc.get("owner_role") or "Coding-Agent").strip() or "Coding-Agent"
    review_role = str((((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("review_role")) or "Review-Agent")
    qa_role = str((((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("qa_role")) or "QA-Agent")
    verbose = _env_truthy("TEAMOS_SELF_UPGRADE_VERBOSE", "0")
    max_attempts = max(1, int(os.getenv("TEAMOS_SELF_UPGRADE_DELIVERY_MAX_ATTEMPTS", "2") or "2"))
    ship_enabled = _env_truthy("TEAMOS_SELF_UPGRADE_SHIP_ENABLED", "1")

    doc, worktree_root, _ = _ensure_task_worktree(ledger_path, doc)
    agent_ids = _register_delivery_agents(db=db, task_doc=doc)
    _update_task_state(ledger_path, doc, status="doing", stage="coding", owner_role=owner_role, extra_execution={"last_error": "", "last_feedback": []})
    _append_markdown(logs_dir, "03_work.md", "Delivery Started", [f"task_id: {task_id}", f"worktree: {worktree_root}", f"owner_role: {owner_role}"])
    _append_metric(logs_dir, event_type="SELF_UPGRADE_DELIVERY_STARTED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="delivery started", payload={"worktree": str(worktree_root)})
    _emit_event(db, event_type="SELF_UPGRADE_TASK_DELIVERY_STARTED", actor=actor, task_doc=doc, payload={"task_id": task_id, "ledger_path": str(ledger_path), "worktree": str(worktree_root)})

    feedback: list[str] = []
    last_review = DeliveryReviewResult(approved=False)
    last_qa = DeliveryQAResult(approved=False)
    try:
        for attempt in range(1, max_attempts + 1):
            doc = _load_yaml(ledger_path)
            exec_state = _execution_state(doc)
            exec_state["attempt_count"] = attempt
            doc = _update_task_state(ledger_path, doc, status="doing", stage="coding", owner_role=owner_role, extra_execution={"attempt_count": attempt, "last_feedback": feedback})
            _set_agent_state(db, agent_ids, "Scheduler-Agent", state="RUNNING", action=f"dispatching attempt {attempt}")
            _set_agent_state(db, agent_ids, owner_role, state="RUNNING", action=f"implementing attempt {attempt}")
            impl = _run_coding_stage(task_doc=doc, worktree_root=worktree_root, feedback=feedback, verbose=verbose)
            changed_files = _changed_files(worktree_root)
            if changed_files and not impl.changed_files:
                impl.changed_files = changed_files[:50]
            _append_markdown(logs_dir, "03_work.md", f"Coding Attempt {attempt}", [impl.summary or "(no summary)", *[f"changed_file: {p}" for p in impl.changed_files], *[f"unresolved: {u}" for u in impl.unresolved]])
            _append_metric(logs_dir, event_type="SELF_UPGRADE_CODING_ATTEMPT", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message=f"coding attempt {attempt}", payload=impl.model_dump())
            _set_agent_state(db, agent_ids, owner_role, state="DONE", action=f"coding attempt {attempt} finished")

            doc = _load_yaml(ledger_path)
            doc = _update_task_state(ledger_path, doc, status="doing", stage="review", owner_role=owner_role, extra_execution={"active_role": review_role})
            _set_agent_state(db, agent_ids, review_role, state="RUNNING", action=f"reviewing attempt {attempt}")
            last_review = _run_review_stage(task_doc=doc, worktree_root=worktree_root, verbose=verbose)
            _append_markdown(logs_dir, "04_test.md", f"Review Attempt {attempt}", [last_review.summary or "(no summary)", *[f"feedback: {x}" for x in last_review.feedback]])
            _append_metric(logs_dir, event_type="SELF_UPGRADE_REVIEW_RESULT", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message=f"review attempt {attempt}", payload=last_review.model_dump())
            _set_agent_state(db, agent_ids, review_role, state="DONE" if last_review.approved else "FAILED", action=f"review attempt {attempt} {'approved' if last_review.approved else 'rejected'}")
            if not last_review.approved:
                feedback = list(last_review.feedback or ([last_review.summary] if last_review.summary else ["review rejected the task"]))
                doc = _load_yaml(ledger_path)
                doc = _update_task_state(ledger_path, doc, status="doing", stage="coding", owner_role=owner_role, extra_execution={"last_feedback": feedback, "last_error": "review_rejected"})
                continue

            doc = _load_yaml(ledger_path)
            doc = _update_task_state(ledger_path, doc, status="test", stage="qa", owner_role=owner_role, extra_execution={"active_role": qa_role})
            _set_agent_state(db, agent_ids, qa_role, state="RUNNING", action=f"running QA attempt {attempt}")
            last_qa = _run_qa_stage(task_doc=doc, worktree_root=worktree_root, verbose=verbose)
            _append_markdown(logs_dir, "04_test.md", f"QA Attempt {attempt}", [last_qa.summary or "(no summary)", *[f"command: {x}" for x in last_qa.commands], *[f"failure: {x}" for x in last_qa.failures]])
            _append_metric(logs_dir, event_type="SELF_UPGRADE_QA_RESULT", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message=f"qa attempt {attempt}", payload=last_qa.model_dump())
            _set_agent_state(db, agent_ids, qa_role, state="DONE" if last_qa.approved else "FAILED", action=f"qa attempt {attempt} {'approved' if last_qa.approved else 'rejected'}")
            if not last_qa.approved:
                feedback = list(last_qa.failures or ([last_qa.summary] if last_qa.summary else ["qa rejected the task"]))
                doc = _load_yaml(ledger_path)
                doc = _update_task_state(ledger_path, doc, status="doing", stage="coding", owner_role=owner_role, extra_execution={"last_feedback": feedback, "last_error": "qa_rejected"})
                continue

            doc = _load_yaml(ledger_path)
            doc = _update_task_state(ledger_path, doc, status="release", stage="release", owner_role=owner_role, extra_execution={"active_role": "Release-Agent"})
            _set_agent_state(db, agent_ids, "Release-Agent", state="RUNNING", action="shipping validated task")
            if dry_run or not ship_enabled:
                release_result = {"branch": _execution_state(doc).get("branch_name") or "", "base_branch": _execution_state(doc).get("base_branch") or "main", "commit_sha": "", "pull_request_url": "", "issue_url": _issue_url(doc), "staged_files": _changed_files(worktree_root)}
            else:
                release_result = _release_task(task_doc=doc, ledger_path=ledger_path, worktree_root=worktree_root)
            doc = _load_yaml(ledger_path)
            doc = _update_task_state(
                ledger_path,
                doc,
                status="closed",
                stage="closed",
                owner_role=owner_role,
                extra_execution={
                    "active_role": "Release-Agent",
                    "commit_sha": str(release_result.get("commit_sha") or ""),
                    "pull_request_url": str(release_result.get("pull_request_url") or ""),
                    "closed_at": _utc_now_iso(),
                    "issue_url": str(release_result.get("issue_url") or ""),
                },
            )
            _set_agent_state(db, agent_ids, "Release-Agent", state="DONE", action="task released")
            _finish_delivery_agents(db, agent_ids, state="DONE", action="delivery finished")
            _append_markdown(logs_dir, "05_release.md", "Release", [f"branch: {release_result.get('branch') or ''}", f"commit_sha: {release_result.get('commit_sha') or ''}", f"pull_request_url: {release_result.get('pull_request_url') or ''}"])
            _append_metric(logs_dir, event_type="SELF_UPGRADE_DELIVERY_FINISHED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="delivery finished", payload=release_result)
            _emit_event(db, event_type="SELF_UPGRADE_TASK_DELIVERY_FINISHED", actor=actor, task_doc=doc, payload={"task_id": task_id, "release": release_result})
            return {"ok": True, "task_id": task_id, "status": "closed", "attempt_count": attempt, "release": release_result, "worktree": str(worktree_root), "project_id": project_id}

        doc = _load_yaml(ledger_path)
        blocked_reason = "; ".join(feedback[:5]) if feedback else "delivery attempts exhausted"
        doc = _update_task_state(ledger_path, doc, status="blocked", stage="blocked", owner_role=owner_role, extra_execution={"last_feedback": feedback, "last_error": blocked_reason})
        _finish_delivery_agents(db, agent_ids, state="FAILED", action="delivery blocked")
        _append_markdown(logs_dir, "07_retro.md", "Delivery Blocked", [blocked_reason, *[f"feedback: {x}" for x in feedback]])
        _append_metric(logs_dir, event_type="SELF_UPGRADE_DELIVERY_BLOCKED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="delivery blocked", payload={"feedback": feedback, "review": last_review.model_dump(), "qa": last_qa.model_dump()}, severity="ERROR")
        _emit_event(db, event_type="SELF_UPGRADE_TASK_DELIVERY_BLOCKED", actor=actor, task_doc=doc, payload={"task_id": task_id, "feedback": feedback})
        return {"ok": False, "task_id": task_id, "status": "blocked", "feedback": feedback, "worktree": str(worktree_root), "project_id": project_id}
    except Exception as e:
        doc = _load_yaml(ledger_path)
        doc = _update_task_state(ledger_path, doc, status="blocked", stage="blocked", owner_role=owner_role, extra_execution={"last_error": str(e)[:500]})
        _finish_delivery_agents(db, agent_ids, state="FAILED", action="delivery failed")
        _append_markdown(logs_dir, "07_retro.md", "Delivery Failed", [str(e)[:800]])
        _append_metric(logs_dir, event_type="SELF_UPGRADE_DELIVERY_FAILED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="delivery failed", payload={"error": str(e)[:800]}, severity="ERROR")
        _emit_event(db, event_type="SELF_UPGRADE_TASK_DELIVERY_FAILED", actor=actor, task_doc=doc, payload={"task_id": task_id, "error": str(e)[:500]})
        raise


def list_delivery_tasks(*, project_id: str = "", status: str = "") -> list[dict[str, Any]]:
    project_ids: list[str]
    pid = str(project_id or "").strip()
    if pid:
        project_ids = [pid]
    else:
        project_ids = ["teamos"] + [p for p in workspace_store.list_projects() if p != "teamos"]
    status_filter = str(status or "").strip().lower()
    out: list[dict[str, Any]] = []
    for current_pid in project_ids:
        task_dir = _task_ledger_dir(current_pid)
        if not task_dir.exists():
            continue
        for path in sorted(task_dir.glob("*.yaml")):
            doc = _load_yaml(path)
            if not _is_self_upgrade_task(doc):
                continue
            st = _current_status(doc)
            if status_filter and st != status_filter:
                continue
            execution = _execution_state(doc)
            out.append(
                {
                    "task_id": str(doc.get("id") or path.stem),
                    "title": str(doc.get("title") or ""),
                    "project_id": str(doc.get("project_id") or current_pid),
                    "workstream_id": str(doc.get("workstream_id") or "general"),
                    "status": st,
                    "owner_role": str(doc.get("owner_role") or ""),
                    "stage": str(execution.get("stage") or ""),
                    "attempt_count": int(execution.get("attempt_count") or 0),
                    "worktree_path": str(execution.get("worktree_path") or ""),
                    "pull_request_url": str(execution.get("pull_request_url") or ""),
                    "ledger_path": str(path),
                    "issue_url": _issue_url(doc),
                }
            )
    return sorted(out, key=lambda x: (str(x.get("status") or ""), str(x.get("task_id") or "")))


def migrate_legacy_worktrees(*, project_id: str = "", task_id: str = "") -> dict[str, Any]:
    touched: list[dict[str, str]] = []
    moved = 0
    updated = 0
    wanted_task_id = str(task_id or "").strip()
    for task in list_delivery_tasks(project_id=project_id):
        if wanted_task_id and str(task.get("task_id") or "") != wanted_task_id:
            continue
        ledger_path = Path(str(task.get("ledger_path") or "")).expanduser().resolve()
        doc = _load_yaml(ledger_path)
        if not _is_self_upgrade_task(doc):
            continue
        source_repo_root = _source_repo_root(doc)
        repo = doc.get("repo") or {}
        if not isinstance(repo, dict):
            repo = {}
        execution = _execution_state(doc)
        execution_policy = doc.get("execution_policy") or {}
        if not isinstance(execution_policy, dict):
            execution_policy = {}
        target_root = _normalized_task_worktree_root(
            doc,
            task_id=str(doc.get("id") or ledger_path.stem),
            source_repo_root=source_repo_root,
            raw_hint=str(execution.get("worktree_path") or execution_policy.get("worktree_hint") or repo.get("workdir") or ""),
        )
        changed = False
        for raw in (
            execution.get("worktree_path"),
            execution_policy.get("worktree_hint"),
            repo.get("workdir"),
        ):
            legacy_root = _absolute_path(raw)
            if legacy_root is None or legacy_root == source_repo_root or legacy_root == target_root:
                continue
            if legacy_root.exists():
                _move_worktree_root(source_repo_root=source_repo_root, legacy_root=legacy_root, target_root=target_root)
                moved += 1
            changed = True
            break
        work_item = _task_work_item(doc)
        if work_item and str(work_item.get("worktree_hint") or "") != str(target_root):
            work_item["worktree_hint"] = str(target_root)
            su = doc.get("self_upgrade") or {}
            if isinstance(su, dict):
                su["work_item"] = work_item
                doc["self_upgrade"] = su
            changed = True
        if str(execution.get("worktree_path") or "") != str(target_root):
            execution["worktree_path"] = str(target_root)
            changed = True
        if str(execution_policy.get("worktree_hint") or "") != str(target_root):
            execution_policy["worktree_hint"] = str(target_root)
            changed = True
        current_repo_workdir = _absolute_path(repo.get("workdir"))
        if current_repo_workdir is not None and current_repo_workdir != source_repo_root and str(current_repo_workdir) != str(target_root):
            repo["workdir"] = str(target_root)
            changed = True
        if str(repo.get("source_workdir") or "") != str(source_repo_root):
            repo["source_workdir"] = str(source_repo_root)
            changed = True
        if changed:
            doc["repo"] = repo
            doc["execution_policy"] = execution_policy
            doc["self_upgrade_execution"] = execution
            _write_yaml(ledger_path, doc)
            updated += 1
            touched.append({"task_id": str(doc.get("id") or ledger_path.stem), "ledger_path": str(ledger_path), "worktree_path": str(target_root)})
    return {"ok": True, "updated": updated, "moved": moved, "tasks": touched}


def delivery_summary() -> dict[str, Any]:
    tasks = list_delivery_tasks()
    return {
        "total": len(tasks),
        "queued": len([t for t in tasks if str(t.get("status") or "") == "todo"]),
        "coding": len([t for t in tasks if str(t.get("status") or "") == "doing"]),
        "qa": len([t for t in tasks if str(t.get("status") or "") == "test"]),
        "release": len([t for t in tasks if str(t.get("status") or "") == "release"]),
        "blocked": len([t for t in tasks if str(t.get("status") or "") == "blocked"]),
        "closed": len([t for t in tasks if str(t.get("status") or "") == "closed"]),
    }


def run_delivery_sweep(*, db: Any, actor: str, project_id: str = "", task_id: str = "", dry_run: bool = False, force: bool = False, max_tasks: Optional[int] = None) -> dict[str, Any]:
    candidates = list_delivery_tasks(project_id=project_id)
    wanted_task_id = str(task_id or "").strip()
    out: list[dict[str, Any]] = []
    scanned = 0
    processed = 0
    limit = max(1, int(max_tasks or os.getenv("TEAMOS_SELF_UPGRADE_DELIVERY_MAX_TASKS_PER_SWEEP", "1") or "1"))
    for task in candidates:
        if wanted_task_id and str(task.get("task_id") or "") != wanted_task_id:
            continue
        if (not wanted_task_id) and str(task.get("status") or "") not in ("todo", "doing", "test", "release"):
            continue
        scanned += 1
        ledger_path = Path(str(task.get("ledger_path") or "")).expanduser().resolve()
        doc = _load_yaml(ledger_path)
        result = execute_task_delivery(db=db, actor=actor, ledger_path=ledger_path, doc=doc, dry_run=dry_run, force=force)
        out.append(result)
        if not result.get("skipped"):
            processed += 1
        if not wanted_task_id and processed >= limit:
            break
    overall_ok = all(bool(item.get("ok")) or bool(item.get("skipped")) for item in out)
    return {"ok": overall_ok, "scanned": scanned, "processed": processed, "tasks": out, "summary": delivery_summary()}
