import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
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


def _codex_home() -> Path:
    raw = str(os.getenv("CODEX_HOME") or "~/.codex").strip() or "~/.codex"
    return Path(raw).expanduser()


def _auth_json_path() -> Path:
    return _codex_home() / "auth.json"


def _auth_file_login_status() -> tuple[bool, str]:
    p = _auth_json_path()
    if not p.is_file():
        return False, "codex auth.json not found"
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"codex auth.json unreadable: {e}"
    if not isinstance(obj, dict):
        return False, "codex auth.json invalid"
    tokens = obj.get("tokens") if isinstance(obj.get("tokens"), dict) else {}
    access_token = str(tokens.get("access_token") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    auth_mode = str(obj.get("auth_mode") or "").strip()
    if access_token or refresh_token:
        detail = auth_mode or "oauth"
        return True, f"codex auth.json available ({detail})"
    return False, "codex auth.json missing access/refresh token"


def codex_login_status() -> tuple[bool, str]:
    try:
        p = _run(["codex", "login", "status"], timeout_sec=10)
    except FileNotFoundError as e:
        ok, msg = _auth_file_login_status()
        if ok:
            return ok, msg
        raise CodexUnavailable("codex CLI not found in PATH and local auth.json is unavailable") from e
    out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (p.stderr or b"").decode("utf-8", errors="replace").strip()
    # codex prints human status to stderr; rely primarily on exit code.
    msg = out or err
    if p.returncode == 0:
        return True, msg or "ok"
    fallback_ok, fallback_msg = _auth_file_login_status()
    if fallback_ok:
        return True, fallback_msg
    return False, msg or fallback_msg or "codex login status failed"


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


def codex_exec_structured(
    *,
    prompt: str,
    schema: dict[str, Any],
    timeout_sec: int = 90,
    model: Optional[str] = None,
) -> CodexResult:
    with tempfile.NamedTemporaryFile(prefix="teamos_codex_schema_", suffix=".json", delete=False, mode="w", encoding="utf-8") as schema_f:
        json.dump(schema, schema_f, ensure_ascii=False, indent=2)
        schema_path = schema_f.name
    try:
        return codex_exec_json(prompt=prompt, schema_path=schema_path, timeout_sec=timeout_sec, model=model)
    finally:
        try:
            os.remove(schema_path)
        except OSError:
            pass
