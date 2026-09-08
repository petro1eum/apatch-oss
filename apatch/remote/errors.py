"""Typed errors for remote workspace operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


# Typed remote failure taxonomy (SPEC-REMOTE-SSH-CORE-1 R5).
# Local controller normalizes every remote failure to one of these stable
# error_type values so callers can branch without parsing free-text messages.
REMOTE_UNREACHABLE = "REMOTE_UNREACHABLE"
REMOTE_APATCH_MISSING = "REMOTE_APATCH_MISSING"
REMOTE_VERSION_SKEW = "REMOTE_VERSION_SKEW"
REMOTE_WORKSPACE_NOT_GIT = "REMOTE_WORKSPACE_NOT_GIT"
REMOTE_ROOT_DENIED = "REMOTE_ROOT_DENIED"
REMOTE_PROTOCOL_ERROR = "REMOTE_PROTOCOL_ERROR"
WORKSPACE_NOT_LOCAL = "WORKSPACE_NOT_LOCAL"

REMOTE_ERROR_TYPES = frozenset(
    {
        REMOTE_UNREACHABLE,
        REMOTE_APATCH_MISSING,
        REMOTE_VERSION_SKEW,
        REMOTE_WORKSPACE_NOT_GIT,
        REMOTE_ROOT_DENIED,
        REMOTE_PROTOCOL_ERROR,
        WORKSPACE_NOT_LOCAL,
    }
)


@dataclass(frozen=True)
class RemoteTaskError(Exception):
    """Structured error that can be surfaced without losing recovery context."""

    error_type: str
    message: str
    recoverable: bool = True
    recommended_action: Optional[str] = None

    def to_result(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ok": False,
            "error_type": self.error_type,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.recommended_action:
            result["recommended_action"] = self.recommended_action
        result["state_update"] = {
            "phase": "failed",
            "next_action": self.recommended_action
            or "Inspect the typed remote error and retry the governed remote step.",
            "risk_level": "medium" if self.recoverable else "high",
        }
        return result
