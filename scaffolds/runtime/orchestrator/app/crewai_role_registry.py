"""Compatibility wrapper for role library registry."""

import sys

from .role_library import registry as _impl

sys.modules[__name__] = _impl
