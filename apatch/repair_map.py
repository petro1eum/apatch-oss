"""Compatibility alias for the open bundled repair-map workflow."""

import importlib as _importlib
import sys as _sys

_impl = _importlib.import_module("apatch_search_workflows.repair_map")
_sys.modules[__name__] = _impl
