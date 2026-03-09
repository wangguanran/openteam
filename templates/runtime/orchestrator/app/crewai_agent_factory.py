"""Compatibility wrapper for CrewAI agent construction."""

import sys

from .engines.crewai import agent_factory as _impl

sys.modules[__name__] = _impl
