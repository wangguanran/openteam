"""Compatibility wrapper for CrewAI spec loading."""

import sys

from .engines.crewai import spec_loader as _impl

sys.modules[__name__] = _impl
