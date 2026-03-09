"""Compatibility wrapper for the CrewAI runtime module."""

import sys

from .engines.crewai import runtime as _impl

sys.modules[__name__] = _impl
