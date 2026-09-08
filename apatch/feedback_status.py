"""Compatibility alias for the open bundled feedback vocabulary."""

import importlib as _importlib
import sys as _sys

_impl = _importlib.import_module("apatch_search_workflows.feedback_status")
_sys.modules[__name__] = _impl
