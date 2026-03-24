from __future__ import annotations

import importlib.util
from pathlib import Path


_IMPL_PATH = Path(__file__).resolve().parents[3] / "openteam_yaml.py"
_SPEC = importlib.util.spec_from_file_location("openteam_yaml", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - defensive
    raise ImportError(f"unable to load YAML compatibility module: {_IMPL_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

YAMLError = _MODULE.YAMLError
load = _MODULE.load
safe_load = _MODULE.safe_load
dump = _MODULE.dump
safe_dump = _MODULE.safe_dump

__all__ = ["YAMLError", "load", "safe_load", "dump", "safe_dump"]

