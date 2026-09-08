"""SPEC-INTERFERENCE-3 R5 — failure taxonomy for stale / schedule / order blocked."""

from __future__ import annotations

from apatch.failure_taxonomy import (
    ERROR_SPEC_INTERFERENCE_STALE,
    ERROR_SPEC_RUN_ORDER_BLOCKED,
    ERROR_SPEC_SCHEDULE_BLOCKED,
    RECOMMENDED_ACTION,
    classify_failure,
)


def test_stale_and_order_blocked_types():
    assert RECOMMENDED_ACTION[ERROR_SPEC_INTERFERENCE_STALE] == "re_run_interference"
    assert RECOMMENDED_ACTION[ERROR_SPEC_SCHEDULE_BLOCKED] == "resolve_conflicts"
    assert RECOMMENDED_ACTION[ERROR_SPEC_RUN_ORDER_BLOCKED] == "complete_predecessor_first"

    stale = classify_failure(
        {
            "ok": False,
            "error_type": ERROR_SPEC_INTERFERENCE_STALE,
            "error": "stale snapshot",
            "recommended_action": "re_run_interference",
        },
        "apatch_spec_run",
    )
    assert stale is not None
    assert stale.error_type == ERROR_SPEC_INTERFERENCE_STALE
    assert stale.recommended_action == "re_run_interference"

    order = classify_failure(
        {
            "ok": False,
            "error_type": ERROR_SPEC_RUN_ORDER_BLOCKED,
            "error": "predecessors not attested",
            "blocked_by": ["SPEC-A"],
        },
        "apatch_spec_run",
    )
    assert order is not None
    assert order.error_type == ERROR_SPEC_RUN_ORDER_BLOCKED
    assert order.recommended_action == "complete_predecessor_first"

    sched = classify_failure(
        {
            "ok": False,
            "error_type": ERROR_SPEC_SCHEDULE_BLOCKED,
            "error": "not schedulable",
        },
        "apatch_spec_run_multi",
    )
    assert sched is not None
    assert sched.error_type == ERROR_SPEC_SCHEDULE_BLOCKED
