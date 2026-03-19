"""Compatibility wrapper for CrewAI team registry."""

import sys

from .engines.crewai import team_registry as _impl

sys.modules[__name__] = _impl
