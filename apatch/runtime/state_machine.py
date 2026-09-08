"""Session lifecycle state machine — allowed mutation operations (RFP-004 §2.3)."""

from __future__ import annotations

from typing import Dict, Optional, Set

from apatch.enforcement import is_enforcement_enabled
from apatch.runtime.hints import hint_for_transition, hint_session_start, hint_session_state
from apatch.runtime.domain import (
    LIFECYCLE_APPLYING,
    LIFECYCLE_ATTESTED,
    LIFECYCLE_COMMITTED,
    LIFECYCLE_DRAFT,
    LIFECYCLE_ENDED,
    LIFECYCLE_FAILED,
    LIFECYCLE_PLANNED,
    LIFECYCLE_ROLLED_BACK,
    LIFECYCLE_VERIFYING,
    derive_lifecycle,
)
from apatch.runtime.errors import RuntimeTransitionError
from apatch.session_state import load_session_state

OP_PLAN = "plan"
OP_APPLY = "apply"
OP_APPLY_SESSION = "apply_session"
OP_STRIP = "strip"
OP_PIPELINE = "pipeline"
OP_VERIFY = "verify"
OP_ATTEST = "attest"
OP_ROLLBACK = "rollback"
OP_REPLAY = "replay"

_MUTATION_OPS = frozenset({OP_PLAN, OP_APPLY, OP_APPLY_SESSION, OP_STRIP, OP_PIPELINE})

_ALLOWED: Dict[str, Set[str]] = {
    LIFECYCLE_DRAFT: {OP_PLAN, OP_APPLY, OP_APPLY_SESSION, OP_STRIP, OP_PIPELINE},
    LIFECYCLE_PLANNED: {OP_PLAN, OP_APPLY, OP_APPLY_SESSION, OP_STRIP, OP_PIPELINE},
    LIFECYCLE_APPLYING: {OP_APPLY, OP_APPLY_SESSION, OP_STRIP, OP_VERIFY, OP_PIPELINE},
    LIFECYCLE_VERIFYING: {OP_VERIFY, OP_ATTEST, OP_ROLLBACK, OP_PIPELINE, OP_REPLAY},
    LIFECYCLE_COMMITTED: {OP_ATTEST, OP_ROLLBACK, OP_VERIFY, OP_REPLAY},
    LIFECYCLE_FAILED: {OP_ROLLBACK, OP_PLAN, OP_APPLY_SESSION, OP_STRIP},
    LIFECYCLE_ROLLED_BACK: {OP_PLAN, OP_APPLY_SESSION, OP_STRIP},
    LIFECYCLE_ATTESTED: {OP_VERIFY, OP_PLAN, OP_REPLAY},
    LIFECYCLE_ENDED: {OP_REPLAY, OP_VERIFY},
}


def current_lifecycle(target_dir: str) -> str:
    raw = load_session_state(target_dir)
    failure = raw.get("failure") if isinstance(raw.get("failure"), dict) else None
    return derive_lifecycle(
        raw.get("phase", "idle"),
        attested=bool(raw.get("attested")),
        failure=failure if not raw.get("ended_at") else None,
        ended=bool(raw.get("ended_at")),
    )


def _session_block_message(target_dir: str) -> Optional[tuple[str, str]]:
    """Human message + MCP hint for enforce-mode session gate."""
    if not is_enforcement_enabled(target_dir):
        return None
    raw = load_session_state(target_dir)
    if raw.get("ended_at"):
        return (
            "Session ended. Start a governed session before mutations.",
            hint_session_start(),
        )
    if not raw.get("session_id") or not raw.get("intent"):
        return (
            "Governed session required (enforce mode).",
            hint_session_start(),
        )
    return None


def assert_operation(
    target_dir: str,
    operation: str,
    *,
    strict: Optional[bool] = None,
) -> None:
    """Raise RuntimeTransitionError if operation is illegal for current lifecycle."""
    if strict is None:
        strict = is_enforcement_enabled(target_dir)

    from apatch.runtime.reconcile import reconcile_governed_session

    reconcile_result = reconcile_governed_session(target_dir)

    def _operation_allowed() -> bool:
        lifecycle = current_lifecycle(target_dir)
        if lifecycle == LIFECYCLE_ENDED and operation in (OP_PLAN, OP_APPLY, OP_ATTEST):
            raise RuntimeTransitionError(
                "session ended — start a new session before mutations",
                lifecycle=lifecycle,
                operation=operation,
                hint=hint_session_start(),
                reconcile_applied=reconcile_result.applied,
                effective_lifecycle=lifecycle,
            )
        if operation in _MUTATION_OPS:
            block = _session_block_message(target_dir)
            if block:
                msg, mcp_hint = block
                raise RuntimeTransitionError(
                    msg,
                    lifecycle=lifecycle,
                    operation=operation,
                    hint=mcp_hint,
                    reconcile_applied=reconcile_result.applied,
                    effective_lifecycle=lifecycle,
                )
        if operation == OP_ROLLBACK:
            if not strict:
                return True
            raw = load_session_state(target_dir)
            if raw.get("checkpoint"):
                return True
        allowed = _ALLOWED.get(lifecycle, set())
        return operation in allowed or not strict

    if _operation_allowed():
        return
    if reconcile_result.applied and _operation_allowed():
        return

    lifecycle = current_lifecycle(target_dir)
    if not strict:
        return
    raise RuntimeTransitionError(
        f"operation '{operation}' not allowed in lifecycle '{lifecycle}'",
        lifecycle=lifecycle,
        operation=operation,
        hint="apatch_session_state() then resume per next_action (resume_session)",
        reconcile_applied=reconcile_result.applied,
        effective_lifecycle=lifecycle,
    )


def _hint_for(operation: str, lifecycle: str) -> str:
    return hint_for_transition(operation, lifecycle)
