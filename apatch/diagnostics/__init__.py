"""Unified diagnostics (RFP-022 / SPEC-DIAGNOSTIC-GRAPH-1)."""

from apatch.diagnostics.collect import collect_diagnostics, enrich_verify_failure
from apatch.diagnostics.schema import SCHEMA_VERSION, normalize_diagnostic, validate_diagnostic

__all__ = [
    "SCHEMA_VERSION",
    "collect_diagnostics",
    "enrich_verify_failure",
    "normalize_diagnostic",
    "validate_diagnostic",
]