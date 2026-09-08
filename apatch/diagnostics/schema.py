"""Unified Diagnostic schema (SPEC-DIAGNOSTIC-GRAPH-1)."""

from __future__ import annotations

import uuid
from typing import Any, Dict

SCHEMA_VERSION = 1

REQUIRED_FIELDS = ("id", "source", "type", "severity", "message", "recommended_action")
VALID_SOURCES = frozenset({"clang", "pytest", "spec", "trust", "sandbox", "arch"})
VALID_SEVERITIES = frozenset({"error", "warning"})
VALID_ACTIONS = frozenset({"fix_forward", "rollback", "reduce_scope", "retry_chunk"})


def new_diagnostic_id() -> str:
    return f"diag_{uuid.uuid4().hex[:12]}"


def validate_diagnostic(d: Dict[str, Any]) -> None:
    if not isinstance(d, dict):
        raise ValueError("diagnostic must be a dict")
    version = d.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    for key in REQUIRED_FIELDS:
        if key not in d or d[key] in (None, ""):
            raise ValueError(f"missing required field: {key}")
    if d["source"] not in VALID_SOURCES:
        raise ValueError(f"invalid source: {d['source']!r}")
    if d["severity"] not in VALID_SEVERITIES:
        raise ValueError(f"invalid severity: {d['severity']!r}")
    if d["recommended_action"] not in VALID_ACTIONS:
        raise ValueError(f"invalid recommended_action: {d['recommended_action']!r}")


def normalize_diagnostic(raw: Dict[str, Any], **defaults: Any) -> Dict[str, Any]:
    out = dict(raw)
    out.setdefault("schema_version", SCHEMA_VERSION)
    out.setdefault("id", new_diagnostic_id())
    for key, value in defaults.items():
        if key not in out or out[key] is None:
            out[key] = value
    validate_diagnostic(out)
    return out