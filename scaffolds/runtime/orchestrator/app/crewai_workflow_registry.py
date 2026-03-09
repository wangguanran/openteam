"""Compatibility wrapper for repo improvement workflow registry."""

import sys

from .teams.repo_improvement.registries import workflows as _impl

sys.modules[__name__] = _impl
