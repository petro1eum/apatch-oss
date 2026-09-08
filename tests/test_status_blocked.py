"""Tests for SPEC-STATUS-BLOCKED-1."""

from __future__ import annotations

import json

from apatch.cli_status import build_status_view, format_status_plain_lines
from apatch.project_status import project_status_workspace
from apatch.session_state import PHASE_BLOCKED, save_session_state
from apatch.verify_baseline import save_baseline


def test_session_block_open_and_closed(tmp_path):
    save_session_state(
        str(tmp_path),
        {
            "session_id": "sess_open",
            "intent": "test",
            "phase": "verify",
            "checkpoint": "ckpt1",
            "attested": False,
        },
        force=True,
    )
    out = project_status_workspace(str(tmp_path))
    assert out.get("extensions") == ["session_blocker_v1"]
    sess = out["session"]
    assert sess["session_id"] == "sess_open"
    assert sess["lifecycle"] == "verifying"
    assert "blocker" not in sess

    save_session_state(
        str(tmp_path),
        {"session_id": "sess_open", "intent": "test", "ended_at": "2026-01-01T00:00:00+00:00"},
        force=True,
    )
    out2 = project_status_workspace(str(tmp_path))
    assert out2["session"] is None


def test_blocker_from_verify_failure(tmp_path):
    save_session_state(
        str(tmp_path),
        {
            "session_id": "s1",
            "intent": "x",
            "phase": PHASE_BLOCKED,
            "failure": {
                "error_type": "VERIFY_FAILED",
                "recommended_action": "fix_forward",
                "recoverable": True,
                "message": "verify failed (exit 1)",
                "tool": "apatch_verify_run",
            },
        },
        force=True,
    )
    sess = project_status_workspace(str(tmp_path))["session"]
    blocker = sess["blocker"]
    assert blocker["error_type"] == "VERIFY_FAILED"
    assert blocker["recommended_action"] == "fix_forward"
    assert "rollback" not in blocker["summary"].lower()
    assert len(blocker["summary"]) <= 240


def test_baseline_summary_in_dto(tmp_path):
    save_baseline(
        str(tmp_path),
        verify_command="pytest",
        failures=["tests/old.py::test_legacy", "tests/old2.py::t"],
    )
    save_session_state(
        str(tmp_path),
        {"session_id": "s1", "intent": "x", "phase": "verify"},
        force=True,
    )
    baseline = project_status_workspace(str(tmp_path))["session"]["baseline"]
    assert baseline["pre_existing_failures"] == [
        "tests/old.py::test_legacy",
        "tests/old2.py::t",
    ]
    assert baseline["truncated"] is False


def test_project_status_dto_shape(tmp_path):
    out = project_status_workspace(str(tmp_path))
    assert "session" in out
    assert "extensions" in out


def test_status_shows_blocker_summary(tmp_path):
    save_session_state(
        str(tmp_path),
        {
            "session_id": "s1",
            "intent": "x",
            "phase": PHASE_BLOCKED,
            "failure": {
                "error_type": "VERIFY_FAILED",
                "recommended_action": "fix_forward",
                "recoverable": True,
                "message": "boom",
                "tool": "apatch_verify_run",
            },
        },
        force=True,
    )
    dto = build_status_view(str(tmp_path))
    lines = format_status_plain_lines(dto)
    assert any(line.startswith("Blocker:") for line in lines)
    assert any("fix_forward" in line for line in lines)
