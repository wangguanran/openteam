"""Compatibility wrapper for repo improvement role registry."""

import sys

from .teams.repo_improvement.registries import roles as _impl

sys.modules[__name__] = _impl
