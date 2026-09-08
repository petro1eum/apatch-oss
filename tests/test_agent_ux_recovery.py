"""SPEC-AGENT-UX-1 (RFP-027) — recovery & UX hardening tests."""
import json
import os

import pytest


def test_r0_self_coverage_rfp_027():
    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(root, "docs", "RFP-027-agent-ux-recovery.md"),
              encoding="utf-8") as f:
        rfp = f.read()
    with open(os.path.join(root, "docs", "specs", "SPEC-AGENT-UX-1.md"),
              encoding="utf-8") as f:
        spec = f.read()
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-027", spec_id="SPEC-AGENT-UX-1")
    assert out["passed"] is True
    assert not out.get("gaps")


def test_r1_resume_from_failed(tmp_path):
    from apatch.session_state import save_session_state
    from apatch.runtime.runtime import MutationRuntime
    from apatch.runtime.state_machine import (
        current_lifecycle, assert_operation, OP_VERIFY, OP_ATTEST,
    )
    from apatch.runtime.errors import RuntimeTransitionError

    root = str(tmp_path)
    (tmp_path / ".apatch").mkdir()
    save_session_state(root, {
        "session_id": "s1", "intent": "x", "phase": "blocked",
        "failure": {"error_type": "VERIFY_FAILED", "message": "boom"},
    }, force=True)
    assert current_lifecycle(root) == "failed"
    with pytest.raises(RuntimeTransitionError):
        assert_operation(root, OP_VERIFY, strict=True)

    out = MutationRuntime(root).resume_session()
    assert out["ok"] is True and out["resumed"] is True
    assert current_lifecycle(root) == "verifying"
    # the previously-blocked ops are now allowed (no raise)
    assert_operation(root, OP_VERIFY, strict=True)
    assert_operation(root, OP_ATTEST, strict=True)


def test_r2_recovery_hint_executable(tmp_path):
    from apatch.runtime.errors import RuntimeTransitionError
    from apatch.runtime.runtime import MutationRuntime

    # the failure surfaced from a blocked op names resume_session
    d = RuntimeTransitionError("x", lifecycle="failed", operation="verify").to_dict()
    assert d["recommended_action"] == "resume_session"
    # and that action is a real, callable recovery path
    assert callable(getattr(MutationRuntime, "resume_session", None))
    pytest.importorskip("mcp")
    from apatch.mcp import server as S
    assert getattr(S, "apatch_resume_session", None) is not None


def test_r3_gc_preserves_active_backups(tmp_path):
    from apatch.gc import run_gc

    root = str(tmp_path)
    ap = tmp_path / ".apatch"
    ap.mkdir()
    # The governed state knows only the full session id. The rollback checkpoint
    # is persisted by apply_session and must still be protected from concurrent GC.
    (ap / "session_state.json").write_text(json.dumps({
        "session_id": "sessA", "intent": "x", "phase": "verify",
    }), encoding="utf-8")
    (ap / "apply_session.json").write_text(json.dumps({
        "session_id": "sessA", "last_checkpoint": "ckptC",
        "checkpoints": ["ckptC"], "chunks": [[1]], "chunk_index": 1,
    }), encoding="utf-8")
    bdir = ap / "backups" / "ckptC"
    bdir.mkdir(parents=True)
    meta = bdir / "metadata.json"
    meta.write_text(json.dumps({"session_id": "ckptC", "backups": []}), encoding="utf-8")

    # Force rotate pressure so the regression exercises the destructive branch.
    for index in range(55):
        old = ap / "backups" / f"old-{index}.bak"
        old.write_text("x", encoding="utf-8")
        os.utime(old, (float(index), float(index)))

    run_gc(root, mode="safe")
    assert meta.exists(), "active apply-session backup must survive gc safe"
    run_gc(root, mode="rotate")
    assert meta.exists(), "active apply-session backup must survive gc rotate"


def test_r4_rollback_backups_pruned_typed(tmp_path):
    from apatch.workflows import rollback_workspace

    root = str(tmp_path)
    other = tmp_path / ".apatch" / "backups" / "other"
    other.mkdir(parents=True)
    (other / "metadata.json").write_text(
        json.dumps({"session_id": "other", "timestamp": "t", "backups": []}),
        encoding="utf-8")
    out = rollback_workspace(root, session_id="pruned-ckpt", preview=False)
    assert out["ok"] is False
    assert out.get("error_type") == "BACKUPS_PRUNED"


def test_r4_rollback_returns_a_twice_edited_file_to_its_pre_session_state(tmp_path):
    """A session that touched one file twice must roll all the way back.

    Each edit takes its own backup, so restoring the newest one leaves the file
    at a state that never existed outside the middle of the session: not the
    result, and not what came before it. The caller is told the rollback
    succeeded, so nothing else notices.
    """

    from apatch.backup import BackupManager

    target = tmp_path / "src.py"
    target.write_text("ORIGINAL\n", encoding="utf-8")

    manager = BackupManager(str(tmp_path), session_id="ckpt")
    manager.create_backup(str(target), step_index=0)
    target.write_text("EDIT ONE\n", encoding="utf-8")
    manager.create_backup(str(target), step_index=1)
    target.write_text("EDIT TWO\n", encoding="utf-8")

    assert manager.restore_file(str(target)) is True
    assert target.read_text(encoding="utf-8") == "ORIGINAL\n"


def test_r4_rollback_removes_a_file_the_session_created(tmp_path):
    """Rolling back a creation means the file is gone, however often it changed."""

    from apatch.backup import BackupManager

    target = tmp_path / "new.py"
    manager = BackupManager(str(tmp_path), session_id="ckpt")
    manager.create_backup(str(target), step_index=0, action_type="CREATE")
    target.write_text("FIRST\n", encoding="utf-8")
    manager.create_backup(str(target), step_index=1)
    target.write_text("SECOND\n", encoding="utf-8")

    assert manager.restore_file(str(target)) is True
    assert not target.exists()


def test_r5_apply_session_already_complete(tmp_path):
    from apatch.apply_session import run_apply_session

    root = str(tmp_path)
    (tmp_path / ".apatch").mkdir()
    (tmp_path / "patches.jsonl").write_text("", encoding="utf-8")
    sess_path = str(tmp_path / ".apatch" / "apply_session.json")
    with open(sess_path, "w", encoding="utf-8") as f:
        json.dump({
            "target_dir": root, "logs_path": "patches.jsonl",
            "chunks": [[1]], "chunk_index": 1,
            "last_checkpoint": "ckpt1", "checkpoints": ["ckpt1"],
        }, f)
    out = run_apply_session("patches.jsonl", root, session_path=sess_path,
                            no_trustchain=True)
    assert out["continue"] is False
    assert out.get("status") == "SESSION_ALREADY_COMPLETE"