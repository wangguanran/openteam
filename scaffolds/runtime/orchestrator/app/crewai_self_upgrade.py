"""Compatibility wrapper for repo improvement planning logic."""

import sys

from .teams.repo_improvement import planning as _impl

sys.modules[__name__] = _impl
