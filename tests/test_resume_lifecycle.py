"""Gate for REC-390b: apatch_resume_session must leave the session in 'verifying' AFTER
the global MCP wrapper enriches its result.

resume_session itself persists phase='verify', but the wrapper enriches any result without
a `state_update` — and _derive_phase returned the idle default for resume, re-saving
phase='idle'. So the next attest/verify_run re-derived 'draft' and the session could not be
attested (the lifecycle 'flap': resume reports 'verifying' but persists 'idle')."""
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session_binding import hash_session_token
from apatch.runtime.state_machine import current_lifecycle
from apatch.session_state import (
    PHASE_VERIFY,
    _derive_phase,
    enrich_tool_response,
    load_session_state,
    save_session_state,
)


def test_resume_derives_verify_phase():
    # Successful resume/recover must derive their promised phase, not idle.
    assert _derive_phase("apatch_resume_session", {"ok": True, "resumed": True}, None) == PHASE_VERIFY
    assert _derive_phase(
        "apatch_recover",
        {"ok": True, "resumed": True, "resume_mode": "verify"},
        None,
    ) == PHASE_VERIFY
    assert _derive_phase(
        "apatch_recover",
        {"ok": True, "resumed": True, "resume_mode": "reapply"},
        None,
    ) == "apply"


def test_resume_then_enrich_persists_verifying(tmp_path):
    save_session_state(str(tmp_path), {
        "session_id": "s-recover", "intent": "test recovery", "phase": "apply",
        "artifacts": [], "failure": None,
    }, force=True)
    res = MutationRuntime(str(tmp_path)).resume_session()
    assert res["resumed"] is True and res["lifecycle"] == "verifying"
    # the global wrapper enriches results without a state_update — must NOT clobber the phase
    enriched = enrich_tool_response("apatch_resume_session", res, target_dir=str(tmp_path))
    assert (enriched.get("state_update") or {}).get("phase") == "verify"
    # the lifecycle the NEXT operation reads must be 'verifying', not 'draft'
    assert current_lifecycle(str(tmp_path)) == "verifying"


def test_resume_after_verify_rollback_returns_to_apply_for_fix_forward(tmp_path):
    from apatch.runtime.state_machine import OP_APPLY_SESSION, assert_operation
    from apatch.session_state import PHASE_APPLY

    save_session_state(str(tmp_path), {
        "session_id": "s-reapply",
        "intent": "fix forward after rolled-back verify",
        "phase": "blocked",
        "artifacts": [],
        "failure": {
            "error_type": "VERIFY_FAILED",
            "details": {"verify_rollback": True, "rollback_performed": True},
        },
    }, force=True)

    res = MutationRuntime(str(tmp_path)).resume_session()

    assert res["resumed"] is True
    assert res["resume_mode"] == "reapply"
    assert res["lifecycle"] == "applying"
    assert "reset=true" in res["next_action"]
    enriched = enrich_tool_response("apatch_resume_session", res, target_dir=str(tmp_path))
    assert (enriched.get("state_update") or {}).get("phase") == PHASE_APPLY
    assert current_lifecycle(str(tmp_path)) == "applying"
    assert_operation(str(tmp_path), OP_APPLY_SESSION, strict=True)


def test_resume_rotates_and_returns_session_capability(tmp_path):
    old_token_hash = hash_session_token("expired-token")
    save_session_state(str(tmp_path), {
        "session_id": "s-rotate",
        "intent": "test capability rotation",
        "phase": "apply",
        "artifacts": [],
        "failure": {"error_type": "VERIFY_FAILED"},
        "session_capability_version": 1,
        "session_token_hash": old_token_hash,
    }, force=True)

    res = MutationRuntime(str(tmp_path)).resume_session()

    assert res["resumed"] is True
    assert res["session_capability"]["session_id"] == "s-rotate"
    assert res["session_capability"]["session_token"] == res["session_token"]
    persisted = load_session_state(str(tmp_path))
    assert persisted["session_token_hash"] == hash_session_token(res["session_token"])
    assert persisted["session_token_hash"] != old_token_hash


def test_reset_induced_stuck_session_recovers_to_attestable(tmp_path):
    """REC-390b trigger: apply_session(reset=true) clears a completed phase to 'apply'
    (_reset_phase_for_reapply), leaving the session 'applying'. With the resume fix, the
    session must recover to attestable ('verifying') via resume — no git checkout."""
    from apatch.runtime.runtime import MutationRuntime

    save_session_state(str(tmp_path), {
        "session_id": "s-reset", "intent": "x", "phase": "verify", "artifacts": [], "failure": None,
    }, force=True)
    rt = MutationRuntime(str(tmp_path))
    rt._reset_phase_for_reapply()  # the apply_session(reset=true) mechanism
    assert current_lifecycle(str(tmp_path)) == "applying"
    res = rt.resume_session()
    enrich_tool_response("apatch_resume_session", res, target_dir=str(tmp_path))
    # recovered to attestable, not flapped back to draft
    assert current_lifecycle(str(tmp_path)) == "verifying"
