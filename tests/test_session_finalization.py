from __future__ import annotations

import json
from pathlib import Path

from apatch.artifact_governance import register_ephemeral_logs
from apatch.lane_context import load_registry
from apatch.runtime.finalization import load_finalization
from apatch.runtime.session import end_session, start_session
from apatch.sandbox import acquire_lease, load_active_lease


def test_session_end_converges_and_is_idempotent(monkeypatch, tmp_path):
    root = str(tmp_path)
    monkeypatch.setenv("APATCH_LANE", "finalize-a")
    opened = start_session(root, "finalize exactly")
    session_id = opened["session"]["session_id"]
    token = opened["session_token"]
    rel = f".apatch/tmp/{session_id}/patches.jsonl"
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")
    register_ephemeral_logs(
        root,
        rel,
        created_by_tool="test",
        governed_session_id=session_id,
    )
    acquire_lease(
        root,
        ["src/a.py"],
        tool="test",
        governed_session_id=session_id,
    )

    first = end_session(
        root,
        expected_session_id=session_id,
        session_token=token,
        require_binding=True,
    )
    assert first["ok"] is True
    assert first["status"] == "complete"
    assert first["session"]["lifecycle"] == "ended"
    assert not path.exists()
    assert load_active_lease(root) is None
    assert load_registry(root)["lanes"]["finalize-a"]["active"] is False
    assert load_finalization(root, session_id)["status"] == "complete"

    second = end_session(
        root,
        expected_session_id=session_id,
        session_token=token,
        require_binding=True,
    )
    assert second["ok"] is True
    assert second["idempotent"] is True


def test_interrupted_finalization_retries_from_journal(monkeypatch, tmp_path):
    import apatch.artifact_governance as governance

    root = str(tmp_path)
    monkeypatch.setenv("APATCH_LANE", "finalize-retry")
    opened = start_session(root, "retry cleanup")
    session_id = opened["session"]["session_id"]
    token = opened["session_token"]
    original = governance.delete_gc_allowed_session_ephemerals
    calls = {"count": 0}

    def fail_once(workspace, exact_session_id):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("simulated interruption")
        return original(workspace, exact_session_id)

    monkeypatch.setattr(governance, "delete_gc_allowed_session_ephemerals", fail_once)
    interrupted = end_session(
        root,
        expected_session_id=session_id,
        session_token=token,
        require_binding=True,
    )
    assert interrupted["ok"] is False
    assert interrupted["error_type"] == "SESSION_FINALIZATION_INCOMPLETE"
    assert "state_ended" in interrupted["completed_steps"]

    recovered = end_session(
        root,
        expected_session_id=session_id,
        session_token=token,
        require_binding=True,
    )
    assert recovered["ok"] is True
    assert recovered["status"] == "complete"


def test_finalization_runs_bounded_hygiene(monkeypatch, tmp_path):
    root = str(tmp_path)
    monkeypatch.setenv("APATCH_LANE", "finalize-gc")
    opened = start_session(root, "rotate history")
    backups = tmp_path / ".apatch" / "backups"
    for index in range(55):
        path = backups / f"old-{index:03d}" / "file.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(index), encoding="utf-8")

    result = end_session(
        root,
        expected_session_id=opened["session"]["session_id"],
        session_token=opened["session_token"],
        require_binding=True,
    )
    assert result["ok"] is True
    assert result["cleanup"]["history_deleted_count"] >= 5
    assert "deleted" not in result["cleanup"]
    assert len(json.dumps(result)) < 8000

