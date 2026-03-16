"""Compatibility wrapper for CrewAI task registry."""

import sys

from .engines.crewai import task_registry as _impl

sys.modules[__name__] = _impl
