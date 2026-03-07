from __future__ import annotations

import importlib
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


class CrewAIRuntimeError(RuntimeError):
    pass


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
