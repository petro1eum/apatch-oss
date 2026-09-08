"""Failure taxonomy → unified Diagnostic."""

from __future__ import annotations

from typing import List, Optional

from apatch.diagnostics.schema import normalize_diagnostic
from apatch.failure_taxonomy import (
    ERROR_DIRECT_WRITE_BLOCKED,
    ERROR_LEASE_CONFLICT,
    ERROR_LEASE_EXPIRED,
    ERROR_NOTARIZATION_FAILED,
    ERROR_TRUSTCHAIN_REJECTED,
    ERROR_VERIFY_FAILED,
    FailureInfo,
)

_SOURCE_MAP = {
    ERROR_DIRECT_WRITE_BLOCKED: "sandbox",
    ERROR_LEASE_CONFLICT: "sandbox",
    ERROR_LEASE_EXPIRED: "sandbox",
    ERROR_NOTARIZATION_FAILED: "trust",
    ERROR_TRUSTCHAIN_REJECTED: "trust",
    ERROR_VERIFY_FAILED: "trust",
}


def _diag_type(error_type: str) -> str:
    return error_type.lower()


def adapt_failure_info(fi: FailureInfo, *, session_id: Optional[str] = None) -> List[dict]:
    source = _SOURCE_MAP.get(fi.error_type, "trust")
    return [
        normalize_diagnostic(
            {
                "source": source,
                "type": _diag_type(fi.error_type),
                "severity": "error",
                "message": fi.message,
                "recommended_action": fi.recommended_action,
                "session_id": session_id,
                "evidence": {"raw_excerpt": fi.message},
            },
            session_id=session_id,
        )
    ]