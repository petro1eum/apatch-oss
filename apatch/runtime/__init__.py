"""RFP-004 — Mutation Operating Model runtime facade."""

from apatch.runtime.attestation import build_attestation_view, export_attestation_bundle
from apatch.runtime.errors import RuntimeTransitionError
from apatch.runtime.events import emit_domain_event
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.models import Session
from apatch.runtime.session import (
    build_session_view,
    continue_apply_session,
    end_session,
    load_typed_session,
    set_session_intent,
    start_session,
)
from apatch.runtime.verification import build_verification_status
from apatch.runtime.state_machine import assert_operation, current_lifecycle

__all__ = [
    "MutationRuntime",
    "RuntimeTransitionError",
    "assert_operation",
    "build_attestation_view",
    "continue_apply_session",
    "export_attestation_bundle",
    "build_session_view",
    "current_lifecycle",
    "emit_domain_event",
    "end_session",
    "set_session_intent",
    "Session",
    "build_verification_status",
    "load_typed_session",
    "start_session",
]
