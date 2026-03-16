from __future__ import annotations

from typing import Any, Callable

from app.skill_library import registry as skill_registry


SkillFn = Callable[..., dict[str, Any]]
_SKILL_HANDLERS: dict[str, SkillFn] = {}


def register_skill(handler_id: str) -> Callable[[SkillFn], SkillFn]:
    normalized = str(handler_id or "").strip()

    def _decorator(fn: SkillFn) -> SkillFn:
        _SKILL_HANDLERS[normalized] = fn
        return fn

    return _decorator


def execute_skill(skill_id: str, *, context: Any, inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from app.skill_library import repo_skills  # noqa: F401

    spec = skill_registry.skill_spec(skill_id)
    fn = _SKILL_HANDLERS.get(spec.handler_id)
    if fn is None:
        raise KeyError(f"unknown skill handler: {spec.handler_id}")
    return fn(context=context, inputs=inputs, state=state, spec=spec)
