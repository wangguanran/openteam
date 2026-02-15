import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Optional


class CodexUnavailable(Exception):
    pass


class CodexNotLoggedIn(Exception):
    pass


@dataclass(frozen=True)
class CodexResult:
    data: dict[str, Any]
    raw_text: str


def _run(cmd: list[str], *, input_text: Optional[str] = None, timeout_sec: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=(input_text.encode("utf-8") if input_text is not None else None),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
        check=False,
    )


def codex_login_status() -> tuple[bool, str]:
    try:
        p = _run(["codex", "login", "status"], timeout_sec=10)
    except FileNotFoundError as e:
        raise CodexUnavailable("codex CLI not found in PATH") from e
    out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (p.stderr or b"").decode("utf-8", errors="replace").strip()
    # codex prints human status to stderr; rely primarily on exit code.
    msg = out or err
    if p.returncode == 0:
        return True, msg or "ok"
    return False, msg or "codex login status failed"


def require_codex_oauth() -> str:
    ok, msg = codex_login_status()
    if not ok:
        raise CodexNotLoggedIn(msg)
    return msg


def codex_exec_json(
    *,
    prompt: str,
    schema_path: str,
    timeout_sec: int = 90,
    model: Optional[str] = None,
) -> CodexResult:
    """
    Run `codex exec` and parse its last message as JSON.

    Notes:
    - We keep sandbox read-only and run in /tmp to minimize unintended file access.
    - Any failure should be handled by the caller (fallback to heuristic).
    """
    require_codex_oauth()

    with tempfile.NamedTemporaryFile(prefix="teamos_codex_", suffix=".txt", delete=False) as out_f:
        out_path = out_f.name

    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--cd",
        "/tmp",
        "--output-schema",
        schema_path,
        "--output-last-message",
        out_path,
        "-",  # read prompt from stdin
    ]
    if model:
        cmd[2:2] = ["--model", model]

    p = _run(cmd, input_text=prompt, timeout_sec=timeout_sec)
    if p.returncode != 0:
        stderr = (p.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"codex exec failed (code={p.returncode}): {stderr[:4000]}")

    raw = ""
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        data = json.loads(raw)
        return CodexResult(data=data, raw_text=raw)
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass
