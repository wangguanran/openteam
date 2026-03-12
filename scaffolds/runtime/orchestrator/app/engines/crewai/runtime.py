from __future__ import annotations

import importlib
import json
import os
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any


class CrewAIRuntimeError(RuntimeError):
    pass


_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _env_truthy(name: str, default: str = "0") -> bool:
    raw = str(os.getenv(name, default) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _normalize_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def codex_oauth_should_bypass_proxy(*, model: str = "", auth_mode: str = "") -> bool:
    if not _env_truthy("TEAMOS_CREWAI_DISABLE_PROXY_FOR_OAUTH_CODEX", "1"):
        return False
    resolved_model = str(model or os.getenv("TEAMOS_CREWAI_MODEL") or os.getenv("OPENAI_MODEL") or "").strip().lower()
    resolved_auth_mode = str(auth_mode or os.getenv("TEAMOS_CREWAI_AUTH_MODE") or os.getenv("CREWAI_OPENAI_AUTH_MODE") or "").strip().lower()
    return "codex" in resolved_model and resolved_auth_mode == "oauth_codex"


@contextmanager
def suppress_proxy_for_codex_oauth(*, model: str = "", auth_mode: str = ""):
    if not codex_oauth_should_bypass_proxy(model=model, auth_mode=auth_mode):
        yield
        return
    previous = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
    try:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _crewai_storage_dir_name() -> str:
    explicit = str(os.getenv("CREWAI_STORAGE_DIR") or "").strip()
    if explicit:
        return explicit
    teamos_explicit = str(os.getenv("TEAMOS_CREWAI_STORAGE_DIR") or "").strip()
    if teamos_explicit:
        os.environ.setdefault("CREWAI_STORAGE_DIR", teamos_explicit)
        return teamos_explicit
    return Path.cwd().name


def _crewai_user_data_file() -> Path:
    app_name = _crewai_storage_dir_name()
    try:
        import appdirs  # type: ignore

        base = Path(appdirs.user_data_dir(app_name, "CrewAI"))
    except Exception:
        base = Path.home() / ".local" / "share" / app_name
    base.mkdir(parents=True, exist_ok=True)
    return base / ".crewai_user.json"


def _prime_crewai_tracing_user_data() -> bool:
    if not _env_truthy("TEAMOS_SUPPRESS_CREWAI_TRACING_PROMPTS", "1"):
        return False
    path = _crewai_user_data_file()
    try:
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8")) or {}
            except Exception:
                payload = {}
        payload.update(
            {
                "first_execution_done": True,
                "first_execution_at": payload.get("first_execution_at") or time.time(),
                "trace_consent": False,
            }
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _candidate_paths() -> list[Path]:
    out: list[Path] = []
    env_values = [
        str(os.getenv("TEAMOS_CREWAI_SRC_PATH") or "").strip(),
        str(os.getenv("CREWAI_SRC_PATH") or "").strip(),
    ]
    for raw in env_values:
        if not raw:
            continue
        p = _normalize_path(raw)
        if p not in out:
            out.append(p)
    return out


def _is_valid_crewai_src(path: Path) -> bool:
    try:
        return path.is_dir() and (path / "crewai" / "__init__.py").is_file()
    except Exception:
        return False


def _module_uses_selected_src(module: Any, selected: Path) -> bool:
    try:
        module_path = Path(str(getattr(module, "__file__", "") or "")).resolve()
    except Exception:
        return False
    try:
        module_path.relative_to(selected.resolve())
        return True
    except Exception:
        return False


def _purge_crewai_modules() -> None:
    for name in [x for x in list(sys.modules.keys()) if x == "crewai" or x.startswith("crewai.")]:
        sys.modules.pop(name, None)


def _ensure_crewai_from_selected_src(selected: Path) -> None:
    existing = sys.modules.get("crewai")
    if existing is not None and _module_uses_selected_src(existing, selected):
        return

    if existing is not None:
        _purge_crewai_modules()

    importlib.invalidate_caches()
    imported = importlib.import_module("crewai")
    if not _module_uses_selected_src(imported, selected):
        module_path = str(getattr(imported, "__file__", "") or "")
        raise CrewAIRuntimeError(
            "crewai import did not resolve to configured source path; "
            f"selected_path={selected}; module_path={module_path or '(unknown)'}"
        )


def configure_crewai_src_path() -> dict[str, Any]:
    candidates = _candidate_paths()
    selected: Path | None = None
    for p in candidates:
        if _is_valid_crewai_src(p):
            selected = p
            break

    if selected is None:
        return {
            "configured": False,
            "selected_path": "",
            "selected_exists": False,
            "inserted_into_syspath": False,
            "candidates": [str(x) for x in candidates],
        }

    selected_str = str(selected)
    inserted = False
    if selected_str not in sys.path:
        sys.path.insert(0, selected_str)
        inserted = True

    return {
        "configured": True,
        "selected_path": selected_str,
        "selected_exists": True,
        "inserted_into_syspath": inserted,
        "candidates": [str(x) for x in candidates],
    }


def _probe_crewai_uncached() -> dict[str, Any]:
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    primed_prompt = _prime_crewai_tracing_user_data()
    cfg = configure_crewai_src_path()
    out: dict[str, Any] = {
        "configured": bool(cfg.get("configured")),
        "source_path": str(cfg.get("selected_path") or ""),
        "source_exists": bool(cfg.get("selected_exists")),
        "importable": False,
        "version": "",
        "module_path": "",
        "error": "",
        "candidates": list(cfg.get("candidates") or []),
        "tracing_prompt_primed": primed_prompt,
    }
    selected_path = str(cfg.get("selected_path") or "").strip()
    selected = Path(selected_path) if selected_path else None
    try:
        if selected is not None:
            _ensure_crewai_from_selected_src(selected)
        mod = importlib.import_module("crewai")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    out["importable"] = True
    out["version"] = str(getattr(mod, "__version__", "") or "")
    out["module_path"] = str(getattr(mod, "__file__", "") or "")
    out["tracing_prompt_suppressed"] = suppress_crewai_first_time_tracing_prompt()
    return out


def suppress_crewai_first_time_tracing_prompt() -> bool:
    if not _env_truthy("TEAMOS_SUPPRESS_CREWAI_TRACING_PROMPTS", "1"):
        return False
    try:
        tracing_utils = __import__(
            "crewai.events.listeners.tracing.utils",
            fromlist=["set_suppress_tracing_messages", "mark_first_execution_done"],
        )
        set_suppress = getattr(tracing_utils, "set_suppress_tracing_messages", None)
        mark_done = getattr(tracing_utils, "mark_first_execution_done", None)
        if callable(set_suppress):
            set_suppress(True)
        if callable(mark_done):
            mark_done(user_consented=False)
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _probe_crewai_cached() -> dict[str, Any]:
    return _probe_crewai_uncached()


def probe_crewai(*, refresh: bool = False) -> dict[str, Any]:
    if refresh:
        _probe_crewai_cached.cache_clear()
    return dict(_probe_crewai_cached())


def require_crewai_importable(*, refresh: bool = False) -> dict[str, Any]:
    info = probe_crewai(refresh=refresh)
    if info.get("importable"):
        return info
    raise CrewAIRuntimeError(
        "crewai import failed; "
        f"source_path={info.get('source_path') or '(none)'}; "
        f"error={info.get('error') or 'unknown'}; "
        f"candidates={info.get('candidates') or []}"
    )
