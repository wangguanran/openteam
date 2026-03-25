"""Engine-agnostic repository tool definitions.

Extracts tool *logic* from task_runtime._build_repo_tools() into GenericToolDef
instances that any engine can wrap with its native decorator/schema.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from app.engines.base import GenericToolDef


def _run(cmd: list[str], *, cwd: Path, timeout_sec: int = 120) -> dict[str, Any]:
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout_sec, check=False,
        )
        return {"returncode": p.returncode, "stdout": p.stdout or "", "stderr": p.stderr or ""}
    except Exception as e:
        return {"returncode": 1, "stdout": "", "stderr": str(e)[:500]}


def _normalize_path(raw: str) -> str:
    rel = str(raw or "").strip().replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/"):
        return ""
    return rel


def _resolve_safe(repo_root: Path, rel: str) -> Path | None:
    normalized = _normalize_path(rel)
    if not normalized:
        return None
    resolved = (repo_root / normalized).resolve()
    if not str(resolved).startswith(str(repo_root.resolve())):
        return None
    return resolved


def _is_allowed(rel: str, allowed_paths: list[str]) -> bool:
    if not allowed_paths or allowed_paths == ["."]:
        return True
    normalized = _normalize_path(rel)
    if not normalized:
        return False
    for ap in allowed_paths:
        if normalized == ap or normalized.startswith(ap.rstrip("/") + "/"):
            return True
    return False


def _safe_test_command(cmd: str, allowlist: list[str]) -> bool:
    if not cmd:
        return False
    if not allowlist:
        return cmd.startswith("pytest") or cmd.startswith("python -m pytest") or cmd.startswith("python -m unittest")
    for allowed in allowlist:
        if cmd == allowed or cmd.startswith(allowed + " "):
            return True
    return False


def build_generic_repo_tools(
    *,
    repo_root: Path,
    allowed_paths: list[str],
    tests_allowlist: list[str],
) -> dict[str, list[GenericToolDef]]:
    """Build engine-agnostic tool definitions grouped by profile.

    Returns ``{"read": [...], "write": [...], "qa": [...]}``.
    """

    def list_allowed_paths_fn() -> str:
        if not allowed_paths:
            return "No writable paths were provided for this task."
        return "\n".join(f"- {p}" for p in allowed_paths)

    def list_directory_fn(relative_path: str = ".") -> str:
        rel = _normalize_path(relative_path or ".") or "."
        resolved = _resolve_safe(repo_root, rel)
        if resolved is None:
            return f"directory_blocked: {relative_path}"
        if not resolved.exists() or not resolved.is_dir():
            return f"directory_not_found: {rel}"
        rows: list[str] = []
        for child in sorted(resolved.iterdir()):
            try:
                shown = str(child.relative_to(repo_root))
            except Exception:
                shown = child.name
            rows.append(shown + ("/" if child.is_dir() else ""))
            if len(rows) >= 200:
                break
        return "\n".join(rows) or "(empty)"

    def search_repository_fn(pattern: str) -> str:
        pat = str(pattern or "").strip()
        if not pat:
            return "pattern is required"
        targets = allowed_paths or ["."]
        cmd = ["rg", "-n", "--hidden", "--glob", "!.git", pat, *targets]
        out = _run(cmd, cwd=repo_root, timeout_sec=30)
        text = str(out.get("stdout") or out.get("stderr") or "").strip()
        return text[:12000] or "(no matches)"

    def read_file_fn(relative_path: str) -> str:
        rel = _normalize_path(relative_path)
        if not rel:
            return "relative_path is required"
        resolved = _resolve_safe(repo_root, rel)
        if resolved is None:
            return f"read_blocked: {relative_path}"
        if not resolved.exists() or not resolved.is_file():
            return f"file_not_found: {rel}"
        try:
            return resolved.read_text(encoding="utf-8", errors="replace")[:20000]
        except Exception as e:
            return f"read_failed: {e}"

    def write_file_fn(relative_path: str, content: str) -> str:
        rel = _normalize_path(relative_path)
        if not rel:
            return "relative_path is required"
        if not _is_allowed(rel, allowed_paths):
            return f"write_blocked_outside_allowed_paths: {rel}"
        resolved = _resolve_safe(repo_root, rel)
        if resolved is None:
            return f"write_blocked: {relative_path}"
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(str(content or ""), encoding="utf-8")
        return f"wrote: {rel}"

    def edit_file_fn(relative_path: str, old_text: str, new_text: str) -> str:
        rel = _normalize_path(relative_path)
        if not rel:
            return "relative_path is required"
        if not _is_allowed(rel, allowed_paths):
            return f"edit_blocked_outside_allowed_paths: {rel}"
        resolved = _resolve_safe(repo_root, rel)
        if resolved is None:
            return f"edit_blocked: {relative_path}"
        if not resolved.exists() or not resolved.is_file():
            return f"file_not_found: {rel}"
        try:
            current = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"read_failed: {e}"
        if old_text not in current:
            return f"old_text_not_found_in: {rel}"
        updated = current.replace(old_text, new_text, 1)
        resolved.write_text(updated, encoding="utf-8")
        return f"edited: {rel}"

    def git_status_fn() -> str:
        out = _run(["git", "status", "--short"], cwd=repo_root, timeout_sec=15)
        return str(out.get("stdout") or "").strip() or "(clean)"

    def git_diff_fn() -> str:
        cmd = ["git", "diff", "--stat"]
        if allowed_paths and allowed_paths != ["."]:
            cmd.append("--")
            cmd.extend(allowed_paths)
        out = _run(cmd, cwd=repo_root, timeout_sec=30)
        return str(out.get("stdout") or "").strip() or "(no changes)"

    def run_validation_fn(command: str) -> str:
        cmd = str(command or "").strip()
        if not _safe_test_command(cmd, allowlist=tests_allowlist):
            return json.dumps({"ok": False, "error": "command_not_allowed", "command": cmd})
        try:
            parts = shlex.split(cmd)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"parse_failed: {e}", "command": cmd})
        out = _run(parts, cwd=repo_root, timeout_sec=600)
        return json.dumps({
            "ok": int(out.get("returncode", 1)) == 0,
            "command": cmd,
            "returncode": int(out.get("returncode", 1)),
            "stdout": str(out.get("stdout") or "")[-4000:],
            "stderr": str(out.get("stderr") or "")[-4000:],
        })

    def bash_fn(command: str) -> str:
        cmd = str(command or "").strip()
        if not cmd:
            return "command is required"
        out = _run(["bash", "-c", cmd], cwd=repo_root, timeout_sec=120)
        rc = int(out.get("returncode", 1))
        stdout = str(out.get("stdout") or "")[-8000:]
        stderr = str(out.get("stderr") or "")[-4000:]
        return json.dumps({"ok": rc == 0, "returncode": rc, "stdout": stdout, "stderr": stderr})

    # Tool definitions
    t_list_allowed = GenericToolDef(
        name="List Allowed Paths",
        description="Return the repository paths this task is allowed to modify.",
        fn=list_allowed_paths_fn,
    )
    t_list_dir = GenericToolDef(
        name="List Directory",
        description="List files under a repository directory. Use paths relative to the repo root.",
        fn=list_directory_fn,
        parameters={"type": "object", "properties": {"relative_path": {"type": "string", "description": "Directory path relative to repo root"}}, "required": []},
    )
    t_search = GenericToolDef(
        name="Search Repository",
        description="Search repository text using ripgrep and return matching lines.",
        fn=search_repository_fn,
        parameters={"type": "object", "properties": {"pattern": {"type": "string", "description": "Text or regex pattern"}}, "required": ["pattern"]},
    )
    t_read = GenericToolDef(
        name="Read File",
        description="Read a UTF-8 repository file and return its contents.",
        fn=read_file_fn,
        parameters={"type": "object", "properties": {"relative_path": {"type": "string", "description": "File path relative to repo root"}}, "required": ["relative_path"]},
    )
    t_write = GenericToolDef(
        name="Write File",
        description="Write UTF-8 text to an allowed repository file.",
        fn=write_file_fn,
        parameters={"type": "object", "properties": {"relative_path": {"type": "string"}, "content": {"type": "string"}}, "required": ["relative_path", "content"]},
    )
    t_edit = GenericToolDef(
        name="Edit File",
        description="Replace the first occurrence of old_text with new_text in a file.",
        fn=edit_file_fn,
        parameters={"type": "object", "properties": {"relative_path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["relative_path", "old_text", "new_text"]},
    )
    t_git_status = GenericToolDef(
        name="Git Status",
        description="Return the current git status for the task worktree.",
        fn=git_status_fn,
    )
    t_git_diff = GenericToolDef(
        name="Git Diff",
        description="Return the current git diff limited to allowed paths.",
        fn=git_diff_fn,
    )
    t_validation = GenericToolDef(
        name="Run Validation Command",
        description="Run a safe validation command (pytest, unittest) and return JSON output.",
        fn=run_validation_fn,
        parameters={"type": "object", "properties": {"command": {"type": "string", "description": "Shell command to run"}}, "required": ["command"]},
    )
    t_bash = GenericToolDef(
        name="Bash",
        description="Run a shell command and return stdout/stderr as JSON.",
        fn=bash_fn,
        parameters={"type": "object", "properties": {"command": {"type": "string", "description": "Shell command to execute"}}, "required": ["command"]},
    )

    read_defs = [t_list_allowed, t_list_dir, t_search, t_read, t_git_status, t_git_diff]
    write_defs = read_defs + [t_write, t_edit, t_validation, t_bash]
    qa_defs = [t_list_allowed, t_read, t_git_status, t_git_diff, t_validation, t_bash]

    return {"read": read_defs, "write": write_defs, "qa": qa_defs}
