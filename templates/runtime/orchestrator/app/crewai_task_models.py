"""Compatibility wrapper for repo improvement task models."""

import sys

from .teams.repo_improvement import models as _impl

sys.modules[__name__] = _impl
