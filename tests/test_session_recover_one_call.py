from __future__ import annotations

import json

from apatch.lane import lane_state_path_for
from apatch.lane_context import bind_lane_from_kwargs
from apatch.runtime.atomic_io import atomic_write_json, read_json_file
from apatch.runtime.recovery import recover_session
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import start_session
from apatch.runtime.state_machine import OP_APPLY_SESSION, assert_operation, current_lifecycle
from apatch.runtime.session_binding import validate_patch_log_contract
from apatch.sandbox import acquire_lease
from apatch.workflows import generate_patch_jsonl_batch


def _start(monkeypatch, root, lane, intent):
    bind_lane_from_kwargs({})
    monkeypatch.setenv("APATCH_LANE", lane)
    opened = start_session(root, intent)
    assert opened["ok"] is True
    return opened


def test_recover_rotates_capability_for_exact_failed_session(monkeypatch, tmp_path):
    root = str(tmp_path)
    opened = _start(monkeypatch, root, "recover-a", "fix failure")
    session_id = opened["session"]["session_id"]
    path = lane_state_path_for(root, "recover-a", "session_state.json")
    state = read_json_file(path, {})
    state["phase"] = "verify"
    state["failure"] = {"error_type": "VERIFY_FAILED", "details": {}}
    atomic_write_json(path, state)

    result = recover_session(root, session_id)
    assert result["ok"] is True
    assert result["recovery"] == "resumed"
    assert result["session_capability"]["session_id"] == session_id
    assert result["session_capability"]["session_token"] != opened["session_token"]


def test_verify_failed_recover_opens_owned_fix_forward_apply(monkeypatch, tmp_path):
    root = str(tmp_path)
    opened = _start(monkeypatch, root, "recover-fix-forward", "repair failed verify")
    session_id = opened["session"]["session_id"]
    source = tmp_path / "value.py"
    source.write_text("KEEP = 1\n", encoding="utf-8")
    failed_log = tmp_path / ".apatch" / "tmp" / session_id / "failed.jsonl"
    generated = generate_patch_jsonl_batch(
        target_dir=root,
        out_path=str(failed_log),
        governed_session_id=session_id,
        needles=[{
            "target_file": "value.py",
            "find_text": "KEEP = 1",
            "replace_text": "KEEP = 2",
        }],
    )
    assert generated["ok"] is True

    verify = (
        "python3 -c \"from pathlib import Path; "
        "raise SystemExit('KEEP = 2' in Path('value.py').read_text())\""
    )
    failed = MutationRuntime(
        root,
        session_id=session_id,
        session_token=opened["session_token"],
        enforce_binding=True,
    ).apply_session(
        str(failed_log),
        verify=verify,
        reset=True,
        no_trustchain=True,
        quiet=True,
    )

    assert failed["ok"] is False
    assert failed["error_type"] == "VERIFY_FAILED"
    assert failed["recommended_action"] == "fix_forward"
    assert failed["rollback_performed"] is True
    assert source.read_text(encoding="utf-8") == "KEEP = 1\n"
    state = read_json_file(
        lane_state_path_for(root, "recover-fix-forward", "session_state.json"),
        {},
    )
    assert state["failure"]["details"]["rollback_performed"] is True

    recovered = recover_session(root, session_id)

    assert recovered["ok"] is True
    assert recovered["recovery"] == "resumed"
    assert recovered["resume_mode"] == "reapply"
    assert recovered["lifecycle"] == "applying"
    assert current_lifecycle(root) == "applying"
    assert_operation(root, OP_APPLY_SESSION, strict=True)

    corrected_log = tmp_path / ".apatch" / "tmp" / session_id / "corrected.jsonl"
    corrected = generate_patch_jsonl_batch(
        target_dir=root,
        out_path=str(corrected_log),
        governed_session_id=session_id,
        needles=[{
            "target_file": "value.py",
            "find_text": "KEEP = 1",
            "replace_text": "KEEP = 3",
        }],
    )
    assert corrected["ok"] is True
    assert validate_patch_log_contract(
        root,
        str(corrected_log),
        expected_session_id=session_id,
    )["ok"] is True

    applied = MutationRuntime(
        root,
        session_id=session_id,
        session_token=recovered["session_token"],
        enforce_binding=True,
    ).apply_session(
        str(corrected_log),
        reset=True,
        no_trustchain=True,
        quiet=True,
    )
    assert applied["ok"] is True
    assert source.read_text(encoding="utf-8") == "KEEP = 3\n"


def test_recover_closes_completed_session(monkeypatch, tmp_path):
    root = str(tmp_path)
    opened = _start(monkeypatch, root, "recover-done", "already done")
    session_id = opened["session"]["session_id"]
    path = lane_state_path_for(root, "recover-done", "session_state.json")
    state = read_json_file(path, {})
    state["phase"] = "complete"
    state["attested"] = True
    atomic_write_json(path, state)

    result = recover_session(root, session_id)
    assert result["ok"] is True
    assert result["recovery"] == "closed"
    assert read_json_file(path, {})["ended_at"]


def test_recover_ignores_foreign_disjoint_live_lease(monkeypatch, tmp_path):
    root = str(tmp_path)
    first = _start(monkeypatch, root, "recover-owner", "owner")
    second = _start(monkeypatch, root, "recover-waiter", "waiter")
    first_id = first["session"]["session_id"]
    second_id = second["session"]["session_id"]
    acquire_lease(
        root,
        ["src/a.py"],
        tool="owner",
        governed_session_id=first_id,
    )

    result = recover_session(root, second_id)
    assert result["ok"] is True
    assert result["recovery"] == "resumed"
