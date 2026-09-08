"""Tests for SPEC-SESSION-RECOVERY-1 reconcile module."""

from __future__ import annotations

import json

import pytest

from apatch.apply_session import default_session_path, save_session
from apatch.runtime.domain import LIFECYCLE_VERIFYING
from apatch.runtime.errors import RuntimeTransitionError
from apatch.runtime.reconcile import reconcile_governed_session
from apatch.runtime.state_machine import OP_VERIFY, assert_operation
from apatch.session_state import PHASE_IDLE, PHASE_VERIFY, load_session_state, save_session_state


def test_reconcile_verify_phase_not_draft(tmp_path):
    save_session_state(
        str(tmp_path),
        {
            "session_id": "sess1",
            "intent": "work",
            "phase": PHASE_VERIFY,
            "failure": {
                "error_type": "RUNTIME_TRANSITION",
                "message": "operation 'verify' not allowed in lifecycle 'draft'",
            },
        },
        force=True,
    )
    result = reconcile_governed_session(str(tmp_path))
    assert result.applied is True
    assert result.changes.get("failure") is None
    st = load_session_state(str(tmp_path))
    assert st.get("failure") is None
    assert_operation(str(tmp_path), OP_VERIFY, strict=True)


def test_resume_apply_session_mid_chunk(tmp_path):
    save_session_state(
        str(tmp_path),
        {"session_id": "sess1", "intent": "work", "phase": PHASE_IDLE},
        force=True,
    )
    save_session(
        default_session_path(str(tmp_path)),
        {
            "session_id": "sess1",
            "chunk_index": 1,
            "chunks": [[1], [2], [3]],
        },
    )
    result = reconcile_governed_session(str(tmp_path))
    assert result.applied is True
    assert result.changes.get("phase") == "apply"
    assert "apply_session" in (result.next_action or "")


def test_attest_reconcile_from_ledger(tmp_path, monkeypatch):
    save_session_state(
        str(tmp_path),
        {
            "session_id": "sess_att",
            "intent": "work",
            "phase": PHASE_VERIFY,
            "attested": False,
        },
        force=True,
    )

    def fake_entries(_td):
        return (
            [
                {
                    "tool_id": "apatch",
                    "payload": {
                        "action": "attest",
                        "governed_session_id": "sess_att",
                    },
                }
            ],
            True,
        )

    monkeypatch.setattr("apatch.spec._ledger_entries", fake_entries)
    result = reconcile_governed_session(str(tmp_path))
    assert result.applied is True
    assert result.changes.get("attested") is True
    st = load_session_state(str(tmp_path))
    assert st.get("attested") is True


def test_session_end_idempotent_after_partial(tmp_path):
    from apatch.runtime.session import end_session

    save_session_state(
        str(tmp_path),
        {
            "session_id": "sess_end",
            "intent": "work",
            "phase": "complete",
            "attested": True,
        },
        force=True,
    )
    first = end_session(str(tmp_path))
    assert first.get("ok") is True
    save_session_state(
        str(tmp_path),
        {
            "session_id": "sess_end",
            "intent": "work",
            "phase": "complete",
            "attested": True,
            "ended_at": None,
        },
        force=True,
    )
    second = end_session(str(tmp_path))
    assert second.get("ok") is True


def test_verify_allowed_after_reconcile(tmp_path):
    save_session_state(
        str(tmp_path),
        {
            "session_id": "sess_v",
            "intent": "work",
            "phase": PHASE_VERIFY,
            "failure": {
                "message": "operation 'verify' not allowed in lifecycle 'draft'",
            },
        },
        force=True,
    )
    reconcile_governed_session(str(tmp_path))
    assert_operation(str(tmp_path), OP_VERIFY, strict=True)


def test_runtime_transition_resume_hint(tmp_path):
    save_session_state(
        str(tmp_path),
        {"session_id": "s", "intent": "i", "phase": "idle"},
        force=True,
    )
    with pytest.raises(RuntimeTransitionError) as exc:
        assert_operation(str(tmp_path), OP_VERIFY, strict=True)
    d = exc.value.to_dict()
    assert d["recommended_action"] == "resume_session"
    assert not d.get("reconcile_applied")
    assert d.get("effective_lifecycle") == "draft"
