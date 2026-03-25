"""Compatibility wrapper for CrewAI workflow registry."""

import sys

from .engines.crewai import workflow_registry as _impl

sys.modules[__name__] = _impl
