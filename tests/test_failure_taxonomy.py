from apatch.failure_taxonomy import (
    ERROR_ARCH_VIOLATION,
    ERROR_BUDGET_EXCEEDED,
    ERROR_DB_RISK,
    ERROR_DIRECT_WRITE_BLOCKED,
    ERROR_MASS_APPLY_BLOCKED,
    classify_failure,
)


def test_mass_apply_blocked():
    f = classify_failure(
        {"ok": False, "use_tool": "apatch_apply_session", "candidate_count": 100},
        "apatch_apply",
    )
    assert f is not None
    assert f.error_type == ERROR_MASS_APPLY_BLOCKED


def test_budget_exceeded():
    f = classify_failure({"ok": False, "reason": "change_budget_exceeded"}, "apatch_apply")
    assert f is not None
    assert f.error_type == ERROR_BUDGET_EXCEEDED


def test_arch_violations():
    f = classify_failure(
        {"ok": False, "violations": [{"type": "forbidden_import"}]},
        "apatch_arch_check",
    )
    assert f is not None
    assert f.error_type == ERROR_ARCH_VIOLATION


def test_db_violations():
    f = classify_failure(
        {"ok": False, "violations": [{"type": "unsafe_migration"}]},
        "apatch_db_check",
    )
    assert f is not None
    assert f.error_type == ERROR_DB_RISK


def test_sandbox_audit_violation_is_not_db_risk():
    f = classify_failure(
        {"ok": False, "violations": [{"path": "app/a.py", "reason": "direct_write_blocked"}]},
        "apatch_sandbox_audit",
    )
    assert f is not None
    assert f.error_type == ERROR_DIRECT_WRITE_BLOCKED
    assert f.recommended_action == "retry_chunk"
