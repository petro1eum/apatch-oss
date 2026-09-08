"""apply_session(reset=True) must be legal from a frozen 'verifying' lifecycle.

Regression for the remote self-heal deadlock: a frozen apply (SESSION_ALREADY_COMPLETE)
leaves the session in lifecycle 'verifying', where apply_session is otherwise illegal —
so the autopilot's reset=true retry tripped RUNTIME_TRANSITION and never wrote anything.
reset=True re-plans from scratch, so the prior verify/committed phase must not block it.
"""

import json
from pathlib import Path

import pytest

from apatch.apply_session import default_session_path
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.errors import RuntimeTransitionError
from apatch.runtime.state_machine import (
    OP_APPLY_SESSION,
    assert_operation,
    current_lifecycle,
)
from apatch.runtime.domain import LIFECYCLE_APPLYING, LIFECYCLE_VERIFYING
from apatch.session_state import load_session_state, save_session_state


def _freeze_in_verifying(tmp_path) -> MutationRuntime:
    rt = MutationRuntime(str(tmp_path))
    rt.open_session("reset-from-verifying regression")
    raw = load_session_state(str(tmp_path))
    raw["phase"] = "verify"
    raw["failure"] = None
    save_session_state(str(tmp_path), raw, force=True)
    assert current_lifecycle(str(tmp_path)) == LIFECYCLE_VERIFYING
    return rt


def test_apply_blocked_in_verifying_without_reset(tmp_path):
    """Baseline: apply_session is illegal in 'verifying' (gate must still bite)."""
    _freeze_in_verifying(tmp_path)
    with pytest.raises(RuntimeTransitionError):
        assert_operation(str(tmp_path), OP_APPLY_SESSION, strict=True)


def test_reset_phase_for_reapply_unblocks_verifying(tmp_path):
    """reset=True helper moves a frozen 'verifying' session back to 'applying'."""
    rt = _freeze_in_verifying(tmp_path)
    rt._reset_phase_for_reapply()
    assert current_lifecycle(str(tmp_path)) == LIFECYCLE_APPLYING
    # apply is now legal even under strict enforcement — no RuntimeTransitionError.
    assert_operation(str(tmp_path), OP_APPLY_SESSION, strict=True)


def test_reset_discards_completed_apply_state_before_reconcile(tmp_path):
    """A stale completed chunk cursor must not restore ``verifying`` on reset."""
    rt = _freeze_in_verifying(tmp_path)
    apply_state_path = Path(default_session_path(str(tmp_path)))
    apply_state_path.parent.mkdir(parents=True, exist_ok=True)
    apply_state_path.write_text(
        json.dumps(
            {
                "target_dir": str(tmp_path),
                "logs_path": "old-patches.jsonl",
                "chunks": [[{"step_index": 1}]],
                "chunk_index": 1,
                "last_checkpoint": "old-checkpoint",
                "session_id": rt.session_id,
            }
        ),
        encoding="utf-8",
    )

    custom_session_path = tmp_path / ".apatch" / "tmp" / "remote-apply-session.json"
    rt._reset_phase_for_reapply(session_path=str(custom_session_path))

    assert not apply_state_path.exists()
    assert current_lifecycle(str(tmp_path)) == LIFECYCLE_APPLYING
    assert_operation(str(tmp_path), OP_APPLY_SESSION, strict=True)


def test_reset_keeps_foreign_default_apply_state(tmp_path):
    """A custom cursor reset must not delete another session's default cursor."""
    rt = _freeze_in_verifying(tmp_path)
    apply_state_path = Path(default_session_path(str(tmp_path)))
    apply_state_path.parent.mkdir(parents=True, exist_ok=True)
    apply_state_path.write_text(
        json.dumps(
            {
                "target_dir": str(tmp_path),
                "logs_path": "foreign-patches.jsonl",
                "chunks": [[{"step_index": 1}]],
                "chunk_index": 1,
                "session_id": "foreign-session",
            }
        ),
        encoding="utf-8",
    )

    custom_session_path = tmp_path / ".apatch" / "tmp" / "remote-apply-session.json"
    rt._reset_phase_for_reapply(session_path=str(custom_session_path))

    assert apply_state_path.exists()


def test_reset_phase_noop_without_session(tmp_path):
    """Helper is a no-op (no crash) when there is no active session."""
    rt = MutationRuntime(str(tmp_path))
    rt._reset_phase_for_reapply()  # must not raise
