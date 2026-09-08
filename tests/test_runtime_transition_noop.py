"""Regression: a RUNTIME_TRANSITION rejection must be a no-op on session_state.

A RUNTIME_TRANSITION means "valid op, wrong lifecycle phase" (e.g. attest while still
applying). It is a guard rejection, not a work failure — it must NOT be recorded as a
session `failure` (which derives lifecycle='failed') nor change the persisted phase.
Otherwise the session is poisoned: resume → verify → attest never converges, and the
agent is wrongly told to `rollback` (which would discard good, applied work).
"""
from apatch.session_state import enrich_tool_response, load_session_state, save_session_state
from apatch.runtime.state_machine import current_lifecycle


def _seed_applying(tmp_path):
    save_session_state(
        str(tmp_path),
        {
            "session_id": "sess_test",
            "intent": "test",
            "phase": "verify",  # apply_session completed → verify phase (lifecycle 'applying')
            "checkpoint": "ckpt_1",
            "failure": None,
            "attested": False,
            "ended_at": None,
        },
    )


def test_runtime_transition_does_not_poison_session(tmp_path):
    _seed_applying(tmp_path)
    before = current_lifecycle(str(tmp_path))

    # Simulate the MCP attest handler returning a guard rejection.
    rejection = {
        "ok": False,
        "error": "operation 'attest' not allowed in lifecycle 'applying'",
        "error_type": "RUNTIME_TRANSITION",
        "recoverable": True,
        "recommended_action": "resume_session",
        "lifecycle": "applying",
        "operation": "attest",
    }
    out = enrich_tool_response("apatch_attest", rejection, target_dir=str(tmp_path))

    state = load_session_state(str(tmp_path))
    assert state.get("failure") is None, "guard rejection must not persist a failure"
    assert state.get("phase") == "verify", "phase must be unchanged by a guard rejection"
    assert current_lifecycle(str(tmp_path)) == before != "failed"
    # The advice must stay 'resume_session', never the dangerous 'rollback'.
    assert out.get("recommended_action") != "rollback"
    assert out.get("error_type") == "RUNTIME_TRANSITION"
