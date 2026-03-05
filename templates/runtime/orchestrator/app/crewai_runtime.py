from __future__ import annotations

import importlib
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


class CrewAIRuntimeError(RuntimeError):
    pass


_DEFAULT_CONTAINER_CREWAI_SRC = Path("/opt/crewai-src")
_DEFAULT_HOST_CREWAI_SRC = (Path.home() / "Codes" / "crewAI" / "lib" / "crewai" / "src").resolve()


def _normalize_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


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
    for p in (_DEFAULT_CONTAINER_CREWAI_SRC, _DEFAULT_HOST_CREWAI_SRC):
        if p not in out:
            out.append(p)
    return out


def _is_valid_crewai_src(path: Path) -> bool:
    try:
        return path.is_dir() and (path / "crewai" / "__init__.py").is_file()
    except Exception:
        return False


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
    }
    try:
        mod = importlib.import_module("crewai")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    out["importable"] = True
    out["version"] = str(getattr(mod, "__version__", "") or "")
    out["module_path"] = str(getattr(mod, "__file__", "") or "")
    return out


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
