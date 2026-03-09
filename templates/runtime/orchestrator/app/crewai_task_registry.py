"""Compatibility wrapper for repo improvement task registry."""

import sys

from .teams.repo_improvement.registries import tasks as _impl

sys.modules[__name__] = _impl
