"""Compatibility wrapper for repo-improvement runtime models."""

import sys

from .domains.repo_improvement import models as _impl

sys.modules[__name__] = _impl
