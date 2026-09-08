"""Suppress known third-party warnings on newer Python (not apatch bugs)."""

from __future__ import annotations

import sys
import warnings

_CONFIGURED = False


def configure_apatch_warnings() -> None:
    """Call once at process entry before optional trustchain/langchain imports."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if sys.version_info >= (3, 14):
        # trustchain[integrations] eagerly imports langchain_core when installed;
        # langchain still uses pydantic.v1 shims that warn on 3.14+.
        warnings.filterwarnings(
            "ignore",
            message=r".*Pydantic V1 functionality isn't compatible with Python 3\.14.*",
            category=UserWarning,
        )
