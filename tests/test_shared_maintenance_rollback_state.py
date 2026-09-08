import json

from apatch.runtime.session import build_session_view
from apatch.session_state import (
    PHASE_BLOCKED,
    PHASE_IDLE,
    enrich_tool_response,
    load_session_state,
    save_session_state,
)
from apatch.shared_maintenance import ERROR_VERIFY, _verify_failure_result


def test_shared_verify_failure_is_canonical_after_rollback():
    result = _verify_failure_result(
        {
            "ok": False,
            "failures": ["SPEC-A#R1"],
            "broken": [],
            "details": [],
            "maintenance_verify": None,
        },
        checkpoints=["checkpoint-1"],
        rollback_performed=True,
    )

    assert result["ok"] is False
    assert result["error_type"] == ERROR_VERIFY == "VERIFY_FAILED"
    assert result["error"]
    assert result["verify_rollback"] is True
    assert result["rollback_performed"] is True
    assert result["recommended_action"] == "start_new_session"
    assert result["diagnostic_count"] == 1
    assert result["diagnostics"]
    assert "new governed session" in result["agent_next"]
    json.dumps(result)


def test_ended_rolled_back_verify_failure_stays_idle(tmp_path):
    state = load_session_state(str(tmp_path))
    state.update(
        {
            "session_id": "apatch_sess_ended",
            "intent": "shared maintenance",
            "phase": PHASE_BLOCKED,
            "ended_at": "2026-08-01T00:00:00+00:00",
            "failure": {
                "error_type": "UNKNOWN",
                "message": "legacy outer enrichment",
            },
        }
    )
    save_session_state(str(tmp_path), state)

    out = enrich_tool_response(
        "apatch_spec_run_multi",
        {
            "ok": False,
            "error_type": "VERIFY_FAILED",
            "error": "shared acceptance failed",
            "recoverable": True,
            "recommended_action": "start_new_session",
            "verify_rollback": True,
            "rollback_performed": True,
            "agent_next": "Start a new governed session with corrected needles.",
        },
        target_dir=str(tmp_path),
    )

    persisted = load_session_state(str(tmp_path))
    assert out["error_type"] == "VERIFY_FAILED"
    assert out["failure"]["details"] == {
        "verify_rollback": True,
        "rollback_performed": True,
    }
    assert out["state_update"]["phase"] == PHASE_IDLE
    assert persisted["phase"] == PHASE_IDLE
    assert persisted["failure"] is None
    assert persisted["ended_at"] == "2026-08-01T00:00:00+00:00"
    assert build_session_view(str(tmp_path))["session"]["lifecycle"] == "ended"
