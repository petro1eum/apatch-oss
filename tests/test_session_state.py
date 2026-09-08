from apatch.failure_taxonomy import ERROR_TRUSTCHAIN_REJECTED, ERROR_VERIFY_FAILED, classify_failure
from apatch.session_state import (
    PHASE_APPLY,
    PHASE_BLOCKED,
    enrich_tool_response,
    load_session_state,
    read_session_state_workspace,
)


def test_classify_trustchain_rejected():
    f = classify_failure({"ok": False, "rejected": True, "rejection_reason": "never_notarized"}, "apatch_verify_notarization")
    assert f is not None
    assert f.error_type == ERROR_TRUSTCHAIN_REJECTED
    assert f.recommended_action == "retry_chunk"


def test_classify_verify_failed():
    f = classify_failure({"ok": False, "verify_rollback": True}, "apatch_apply_session")
    assert f is not None
    assert f.error_type == ERROR_VERIFY_FAILED
    assert f.recoverable is True


def test_enrich_persists_state(tmp_path):
    result = enrich_tool_response(
        "apatch_apply_session",
        {
            "ok": True,
            "continue": True,
            "checkpoint": "apatch_sess_1",
            "progress": {"chunks_done": 1, "chunks_total": 4},
        },
        target_dir=str(tmp_path),
    )
    assert result["state_update"]["phase"] == PHASE_APPLY
    assert result["state_update"]["checkpoint"] == "apatch_sess_1"
    assert result["state_update"]["budget_remaining"] == 3
    state = load_session_state(str(tmp_path))
    assert state["last_tool"] == "apatch_apply_session"
    assert state["phase"] == PHASE_APPLY


def test_enrich_blocked_on_failure(tmp_path):
    result = enrich_tool_response(
        "apatch_apply_session",
        {"ok": False, "error": "boom", "checkpoint": "ck1"},
        target_dir=str(tmp_path),
    )
    assert result["state_update"]["phase"] == PHASE_BLOCKED
    assert result["error_type"]
    assert result["recommended_action"]


def test_spec_scaffold_prewrite_failure_does_not_block_or_request_rollback(tmp_path):
    result = enrich_tool_response(
        "apatch_spec_scaffold",
        {"ok": False, "error": "RFP not found: /missing/RFP.md"},
        target_dir=str(tmp_path),
    )

    assert result["state_update"]["phase"] == "idle"
    assert result["state_update"]["risk_level"] == "low"
    assert result["state_update"]["next_action"] == (
        "resolve the reported read-only query error and retry"
    )
    assert "failure" not in result
    assert "recommended_action" not in result
    assert load_session_state(str(tmp_path)).get("failure") is None


def test_avatar_sync_credential_failure_is_lifecycle_neutral(tmp_path):
    before = load_session_state(str(tmp_path))
    result = enrich_tool_response(
        "apatch_avatar_evidence_sync",
        {
            "ok": False,
            "status": "credential_unavailable",
            "error_code": "tracker_secret_resolution_denied",
            "retryable": True,
            "outbox_preserved": True,
        },
        target_dir=str(tmp_path),
    )

    assert result["state_update"] == {
        "phase": "idle",
        "next_action": "resolve the reported operational error and retry",
        "risk_level": "low",
        "last_tool": "apatch_avatar_evidence_sync",
    }
    assert result["session_state_write_behind"] is True
    assert "failure" not in result
    assert "recommended_action" not in result
    assert load_session_state(str(tmp_path)) == before


def test_avatar_sync_success_preserves_active_session_state(tmp_path):
    enrich_tool_response(
        "apatch_apply_session",
        {
            "ok": True,
            "continue": True,
            "checkpoint": "apatch_sess_active",
            "progress": {"chunks_done": 1, "chunks_total": 2},
        },
        target_dir=str(tmp_path),
    )
    before = load_session_state(str(tmp_path))

    result = enrich_tool_response(
        "apatch_avatar_evidence_sync",
        {
            "ok": True,
            "status": "synchronized",
            "complete": True,
            "outbox_preserved": True,
        },
        target_dir=str(tmp_path),
    )

    assert result["state_update"]["phase"] == PHASE_APPLY
    assert result["state_update"]["next_action"] == before["next_action"]
    assert result["session_state_write_behind"] is True
    assert load_session_state(str(tmp_path)) == before


def test_read_session_state_workspace(tmp_path):
    enrich_tool_response(
        "apatch_doctor",
        {"ok": True, "trustchain": {"active": True}},
        target_dir=str(tmp_path),
    )
    out = read_session_state_workspace(str(tmp_path))
    assert out["ok"] is True
    assert out["trustchain"] is False
