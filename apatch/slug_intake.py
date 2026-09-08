"""Compatibility alias for the bundled open slug intake workflow."""

import importlib as _importlib
import sys as _sys

_impl = _importlib.import_module("apatch_search_workflows.slug_intake")
_sys.modules[__name__] = _impl
