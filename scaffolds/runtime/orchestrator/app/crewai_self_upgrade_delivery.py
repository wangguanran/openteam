"""Compatibility wrapper for repo improvement delivery logic."""

import sys

from .teams.repo_improvement import delivery as _impl

sys.modules[__name__] = _impl
