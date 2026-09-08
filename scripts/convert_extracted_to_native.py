#!/usr/bin/env python3
"""Backwards-compatible shim. The real implementation now lives inside the
packaged module ``apatch.native_converter`` so it ships in the wheel and works
without hardcoded paths. Prefer ``python -m apatch.native_converter``.
"""
from __future__ import annotations

import os
import sys

# Make the repo root importable when run directly from a source checkout.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Re-export the full public API so legacy imports
# (``from scripts.convert_extracted_to_native import ...``) keep working.
from apatch.native_converter import (  # noqa: E402,F401
    convert,
    emit_register_cpp,
    extract_callees,
    main,
    replace_keyword_safe,
    resolve_minimal_includes,
    transform_body,
    SYMBOL_INCLUDES,
)

if __name__ == "__main__":
    raise SystemExit(main())
