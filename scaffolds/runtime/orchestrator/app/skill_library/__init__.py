from __future__ import annotations

import importlib

__all__ = ["builtin_skills"]


def __getattr__(name: str):
    if name == "builtin_skills":
        return importlib.import_module("app.skill_library.builtin_skills")
    raise AttributeError(name)
