"""Runtime errors (RFP-004 E1)."""

from __future__ import annotations


class RuntimeTransitionError(Exception):
    """Illegal lifecycle transition for a mutation operation."""

    def __init__(
        self,
        message: str,
        *,
        lifecycle: str | None = None,
        operation: str | None = None,
        hint: str | None = None,
        reconcile_applied: bool = False,
        effective_lifecycle: str | None = None,
    ):
        super().__init__(message)
        self.lifecycle = lifecycle
        self.operation = operation
        self.hint = hint
        self.reconcile_applied = reconcile_applied
        self.effective_lifecycle = effective_lifecycle

    def to_dict(self) -> dict:
        out = {
            "ok": False,
            "error": str(self),
            "error_type": "RUNTIME_TRANSITION",
            "recoverable": True,
            "recommended_action": "resume_session",
        }
        if self.lifecycle:
            out["lifecycle"] = self.lifecycle
        if self.operation:
            out["operation"] = self.operation
        if self.hint:
            out["hint"] = self.hint
            out["agent_next"] = self.hint
        if self.reconcile_applied:
            out["reconcile_applied"] = True
        if self.effective_lifecycle:
            out["effective_lifecycle"] = self.effective_lifecycle
        return out


class SessionBindingError(Exception):
    """A caller does not own the governed session or immutable artifact."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "SESSION_MISMATCH",
        expected: str | None = None,
        actual: str | None = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.expected = expected
        self.actual = actual

    def to_dict(self) -> dict:
        out = {
            "ok": False,
            "error": str(self),
            "error_type": self.error_type,
            "recoverable": True,
            "recommended_action": "use_session_capability",
        }
        if self.expected is not None:
            out["expected"] = self.expected
        if self.actual is not None:
            out["actual"] = self.actual
        return out


class HygieneCriticalError(Exception):
    """Governed mutations blocked by inference sunset / hygiene critical status."""

    def __init__(
        self,
        message: str,
        *,
        inferred_count: int | None = None,
        ops: int | None = None,
    ):
        super().__init__(message)
        self.inferred_count = inferred_count
        self.ops = ops

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": str(self),
            "error_type": "HYGIENE_CRITICAL",
            "recoverable": True,
            "recommended_action": "reduce_scope",
        }
