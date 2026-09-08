"""Integration matrix SPEC-SESSION-RECOVERY-1 R7 — RFP-021 §4.3 six scenarios."""

from __future__ import annotations

import json
import os

import pytest

from apatch.apply_session import default_session_path, save_session
from apatch.runtime.reconcile import reconcile_governed_session
from apatch.runtime.session import end_session
from apatch.runtime.state_machine import OP_APPLY_SESSION, OP_ATTEST, OP_VERIFY, assert_operation
from apatch.session_state import (
    PHASE_APPLY,
    PHASE_COMPLETE,
    PHASE_VERIFY,
    load_session_state,
    save_session_state,
)
from apatch.verify_jobs import jobs_dir


def _base_state(**overrides):
    state = {
        "session_id": "s",
        "intent": "matrix",
        "phase": "idle",
    }
    state.update(overrides)
    return state


def _write_verify_job(tmp_path, session_id: str, *, state: str, ok: bool = True) -> None:
    root = str(tmp_path)
    os.makedirs(jobs_dir(root), exist_ok=True)
    job = {
        "job_id": "vjob_test_matrix",
        "state": state,
        "session_id": session_id,
        "started_at": "2026-06-12T00:00:00+00:00",
        "finished_at": "2026-06-12T00:01:00+00:00" if state != "running" else None,
        "verify_result": {"ok": ok},
    }
    path = os.path.join(jobs_dir(root), "vjob_test_matrix.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f)


@pytest.mark.parametrize(
    "scenario,setup,operation,check",
    [
        (
            "mid_apply_session",
            {
                "phase": "idle",
                "apply": {"session_id": "s", "chunk_index": 1, "chunks": [[1], [2], [3], [4], [5]]},
            },
            OP_APPLY_SESSION,
            lambda st: st.get("phase") == PHASE_APPLY,
        ),
        (
            "mid_sync_verify_run",
            {
                "phase": PHASE_VERIFY,
                "failure_msg": "operation 'verify' not allowed in lifecycle 'draft'",
            },
            OP_VERIFY,
            lambda st: st.get("failure") is None,
        ),
        (
            "post_verify_pre_attest",
            {
                "phase": PHASE_COMPLETE,
                "apply_done": True,
            },
            OP_ATTEST,
            lambda st: st.get("phase") in (PHASE_COMPLETE, PHASE_VERIFY),
        ),
        (
            "mid_attest_ledger_reconcile",
            {
                "phase": PHASE_VERIFY,
                "ledger_attest": True,
            },
            None,
            lambda st: st.get("attested") is True and "session_end" in (st.get("next_action") or ""),
        ),
        (
            "post_attest_pre_session_end",
            {
                "phase": PHASE_VERIFY,
                "attested": True,
            },
            "session_end",
            lambda st: st.get("ended_at") is not None,
        ),
        (
            "mid_session_end_partial_purge",
            {
                "phase": PHASE_COMPLETE,
                "attested": True,
                "partial_end": True,
            },
            "session_end",
            lambda st: st.get("ended_at") is not None,
        ),
    ],
)
def test_reconcile_unblocks_scenario(tmp_path, scenario, setup, operation, check, monkeypatch):
    state = _base_state(phase=setup.get("phase", "idle"))
    if setup.get("failure_msg"):
        state["failure"] = {"message": setup["failure_msg"]}
    if setup.get("attested"):
        state["attested"] = True
    save_session_state(str(tmp_path), state, force=True)

    if setup.get("apply"):
        data = dict(setup["apply"])
        data.setdefault("session_id", "s")
        save_session(default_session_path(str(tmp_path)), data)
    elif setup.get("apply_done"):
        save_session(
            default_session_path(str(tmp_path)),
            {"session_id": "s", "chunk_index": 1, "chunks": [[1]]},
        )

    if setup.get("ledger_attest"):

        def fake_entries(_td):
            return (
                [
                    {
                        "tool_id": "apatch",
                        "payload": {
                            "action": "attest",
                            "governed_session_id": "s",
                        },
                    }
                ],
                True,
            )

        monkeypatch.setattr("apatch.spec._ledger_entries", fake_entries)

    if setup.get("partial_end"):
        first = end_session(str(tmp_path))
        assert first.get("ok") is True
        stale = load_session_state(str(tmp_path))
        stale["ended_at"] = None
        save_session_state(str(tmp_path), stale, force=True)

    reconcile_governed_session(str(tmp_path))

    if operation == OP_APPLY_SESSION:
        assert_operation(str(tmp_path), operation, strict=False)
    elif operation in (OP_VERIFY, OP_ATTEST):
        assert_operation(str(tmp_path), operation, strict=False)
    elif operation == "session_end":
        result = end_session(str(tmp_path))
        assert result.get("ok") is True

    st = load_session_state(str(tmp_path))
    assert check(st), f"{scenario}: post-condition failed: {st}"


def test_async_verify_job_passed_enables_attest(tmp_path):
    save_session_state(
        str(tmp_path),
        _base_state(
            phase="idle",
            failure={"message": "operation 'verify' not allowed in lifecycle 'draft'"},
        ),
        force=True,
    )
    _write_verify_job(tmp_path, "s", state="passed", ok=True)
    reconcile_governed_session(str(tmp_path))
    assert_operation(str(tmp_path), OP_ATTEST, strict=True)
    st = load_session_state(str(tmp_path))
    assert st.get("phase") == PHASE_COMPLETE
    assert st.get("failure") is None


def test_running_verify_reconcile_is_revision_idempotent(tmp_path):
    save_session_state(
        str(tmp_path),
        _base_state(phase=PHASE_VERIFY, failure=None),
        force=True,
    )
    _write_verify_job(tmp_path, "s", state="running", ok=None)
    before = load_session_state(str(tmp_path))

    first = reconcile_governed_session(str(tmp_path))
    after_first = load_session_state(str(tmp_path))
    second = reconcile_governed_session(str(tmp_path))
    after = load_session_state(str(tmp_path))

    assert first.applied is True
    assert second.applied is False
    assert after_first["revision"] == before["revision"] + 1
    assert after["revision"] == after_first["revision"]


def test_verify_refreshes_capability_after_reconcile(tmp_path):
    """A legitimate reconcile write must not invalidate the caller's CAS token."""
    from apatch.runtime.runtime import MutationRuntime
    from apatch.runtime.session import start_session

    opened = start_session(str(tmp_path), "verify capability refresh")
    session_id = opened["session"]["session_id"]
    session_token = opened["session_token"]
    raw = load_session_state(str(tmp_path))
    raw["phase"] = PHASE_VERIFY
    raw["failure"] = None
    save_session_state(str(tmp_path), raw, force=True)
    _write_verify_job(tmp_path, session_id, state="running", ok=None)

    runtime = MutationRuntime(
        str(tmp_path),
        session_id=session_id,
        session_token=session_token,
        enforce_binding=True,
    )
    result = runtime.verify_run(verify=["/usr/bin/true"])

    assert result["ok"] is True, result
    assert result.get("error_type") != "SESSION_REVISION_MISMATCH"
