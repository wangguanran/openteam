from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable
from app.skill_library import registry as skill_registry


SkillFn = Callable[..., dict[str, Any]]
_SKILL_HANDLERS: dict[str, SkillFn] = {}
_HANDLER_MODULES_LOADED = False


def register_skill(handler_id: str) -> Callable[[SkillFn], SkillFn]:
    normalized = str(handler_id or "").strip()

    def _decorator(fn: SkillFn) -> SkillFn:
        _SKILL_HANDLERS[normalized] = fn
        return fn

    return _decorator


def _load_skill_handler_modules() -> None:
    global _HANDLER_MODULES_LOADED
    if _HANDLER_MODULES_LOADED:
        return
    package_name = "app.skill_library"
    package = importlib.import_module(package_name)
    package_path = getattr(package, "__path__", None)
    if not package_path:
        _HANDLER_MODULES_LOADED = True
        return
    for module_info in pkgutil.iter_modules(package_path):
        name = str(module_info.name or "").strip()
        if not name or name in {"executor", "registry"} or name.startswith("_"):
            continue
        importlib.import_module(f"{package_name}.{name}")
    _HANDLER_MODULES_LOADED = True


def execute_skill(skill_id: str, *, context: Any, inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    _load_skill_handler_modules()
    workflow = getattr(context, "workflow", None)
    team_id = str(getattr(workflow, "team_id", "") or "").strip()
    spec = skill_registry.skill_spec(skill_id, team_id=team_id)
    fn = _SKILL_HANDLERS.get(spec.handler_id)
    if fn is None:
        raise KeyError(f"unknown skill handler: {spec.handler_id}")
    return fn(context=context, inputs=inputs, state=state, spec=spec)
