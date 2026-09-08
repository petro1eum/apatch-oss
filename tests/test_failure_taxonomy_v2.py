"""Failure taxonomy v2 — fix_forward vs rollback (SPEC-FAILURE-TAXONOMY-2)."""

from apatch.failure_taxonomy import ERROR_APPLY_FAILED, ERROR_VERIFY_FAILED, classify_failure


def test_verify_failed_defaults_fix_forward():
    f = classify_failure(
        {"ok": False, "error": "verify failed (exit 1)"},
        "apatch_verify_run",
    )
    assert f is not None
    assert f.error_type == ERROR_VERIFY_FAILED
    assert f.recommended_action == "fix_forward"


def test_verify_rollback_stays_rollback():
    f = classify_failure(
        {"ok": False, "error": "rolled back", "verify_rollback": True},
        "apatch_apply_session",
    )
    assert f is not None
    assert f.recommended_action == "rollback"


def test_verify_rollback_already_performed_fix_forward():
    f = classify_failure(
        {
            "ok": False,
            "error": "rolled back",
            "verify_rollback": True,
            "rollback_performed": True,
        },
        "apatch_apply_session",
    )
    assert f is not None
    assert f.error_type == ERROR_VERIFY_FAILED
    assert f.recommended_action == "fix_forward"
    assert f.details["rollback_performed"] is True


def test_verify_failure_includes_baseline_details():
    f = classify_failure(
        {
            "ok": False,
            "error": "new test failed",
            "baseline": {
                "mode": "compare",
                "new_failures": ["tests/new.py::test_x"],
                "pre_existing_failures": ["tests/old.py::test_legacy"],
            },
        },
        "apatch_verify_run",
    )
    assert f is not None
    assert f.recommended_action == "fix_forward"
    assert f.details["baseline"]["new_failures"] == ["tests/new.py::test_x"]


def test_explicit_verify_failed_survives_wrapper_tool():
    f = classify_failure(
        {
            "ok": False,
            "error_type": "VERIFY_FAILED",
            "error": "nested verify failed",
            "recommended_action": "fix_forward",
        },
        "apatch_spec_run_multi",
    )
    assert f is not None
    assert f.error_type == ERROR_VERIFY_FAILED
    assert f.recommended_action == "fix_forward"


def test_remote_timeout_preserves_reconciliation_action():
    failure = classify_failure(
        {
            "ok": False,
            "error_type": "REMOTE_TIMEOUT",
            "message": "Remote operation timed out.",
            "recoverable": True,
            "recommended_action": "reconcile_remote_state",
        },
        "apatch_remote_task_run",
    )

    assert failure is not None
    assert failure.error_type == "REMOTE_TIMEOUT"
    assert failure.recoverable is True
    assert failure.recommended_action == "reconcile_remote_state"
    assert failure.message == "Remote operation timed out."


def test_apply_failed_still_rollback():
    f = classify_failure(
        {"ok": False, "chunk_result": {"failed": 1}, "error": "chunk failed"},
        "apatch_apply_session",
    )
    assert f is not None
    assert f.error_type == ERROR_APPLY_FAILED
    assert f.recommended_action == "rollback"
