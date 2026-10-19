"""Gate for REC-390b: apatch_resume_session must leave the session in 'verifying' AFTER
the global MCP wrapper enriches its result.

resume_session itself persists phase='verify', but the wrapper enriches any result without
a `state_update` — and _derive_phase returned the idle default for resume, re-saving
phase='idle'. So the next attest/verify_run re-derived 'draft' and the session could not be
attested (the lifecycle 'flap': resume reports 'verifying' but persists 'idle')."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session_binding import hash_session_token
from apatch.runtime.state_machine import current_lifecycle
from apatch.session_state import (
    PHASE_VERIFY,
    _derive_phase,
    enrich_tool_response,
    load_session_state,
    save_session_state,
)


def test_resume_derives_verify_phase():
    # Successful resume/recover must derive their promised phase, not idle.
    assert _derive_phase("apatch_resume_session", {"ok": True, "resumed": True}, None) == PHASE_VERIFY
    assert _derive_phase(
        "apatch_recover",
        {"ok": True, "resumed": True, "resume_mode": "verify"},
        None,
    ) == PHASE_VERIFY
    assert _derive_phase(
        "apatch_recover",
        {"ok": True, "resumed": True, "resume_mode": "reapply"},
        None,
    ) == "apply"


def test_resume_then_enrich_persists_verifying(tmp_path):
    save_session_state(str(tmp_path), {
        "session_id": "s-recover", "intent": "test recovery", "phase": "apply",
        "artifacts": [], "failure": None,
    }, force=True)
    res = MutationRuntime(str(tmp_path)).resume_session()
    assert res["resumed"] is True and res["lifecycle"] == "verifying"
    # the global wrapper enriches results without a state_update — must NOT clobber the phase
    enriched = enrich_tool_response("apatch_resume_session", res, target_dir=str(tmp_path))
    assert (enriched.get("state_update") or {}).get("phase") == "verify"
    # the lifecycle the NEXT operation reads must be 'verifying', not 'draft'
    assert current_lifecycle(str(tmp_path)) == "verifying"


def test_resume_after_verify_rollback_returns_to_apply_for_fix_forward(tmp_path):
    from apatch.runtime.state_machine import OP_APPLY_SESSION, assert_operation
    from apatch.session_state import PHASE_APPLY

    save_session_state(str(tmp_path), {
        "session_id": "s-reapply",
        "intent": "fix forward after rolled-back verify",
        "phase": "blocked",
        "artifacts": [],
        "failure": {
            "error_type": "VERIFY_FAILED",
            "details": {"verify_rollback": True, "rollback_performed": True},
        },
    }, force=True)

    res = MutationRuntime(str(tmp_path)).resume_session()

    assert res["resumed"] is True
    assert res["resume_mode"] == "reapply"
    assert res["lifecycle"] == "applying"
    assert "reset=true" in res["next_action"]
    enriched = enrich_tool_response("apatch_resume_session", res, target_dir=str(tmp_path))
    assert (enriched.get("state_update") or {}).get("phase") == PHASE_APPLY
    assert current_lifecycle(str(tmp_path)) == "applying"
    assert_operation(str(tmp_path), OP_APPLY_SESSION, strict=True)


def test_resume_rotates_and_returns_session_capability(tmp_path):
    old_token_hash = hash_session_token("expired-token")
    save_session_state(str(tmp_path), {
        "session_id": "s-rotate",
        "intent": "test capability rotation",
        "phase": "apply",
        "artifacts": [],
        "failure": {"error_type": "VERIFY_FAILED"},
        "session_capability_version": 1,
        "session_token_hash": old_token_hash,
    }, force=True)

    res = MutationRuntime(str(tmp_path)).resume_session()

    assert res["resumed"] is True
    assert res["session_capability"]["session_id"] == "s-rotate"
    assert res["session_capability"]["session_token"] == res["session_token"]
    persisted = load_session_state(str(tmp_path))
    assert persisted["session_token_hash"] == hash_session_token(res["session_token"])
    assert persisted["session_token_hash"] != old_token_hash


def test_reset_induced_stuck_session_recovers_to_attestable(tmp_path):
    """REC-390b trigger: apply_session(reset=true) clears a completed phase to 'apply'
    (_reset_phase_for_reapply), leaving the session 'applying'. With the resume fix, the
    session must recover to attestable ('verifying') via resume — no git checkout."""
    from apatch.runtime.runtime import MutationRuntime

    save_session_state(str(tmp_path), {
        "session_id": "s-reset", "intent": "x", "phase": "verify", "artifacts": [], "failure": None,
    }, force=True)
    rt = MutationRuntime(str(tmp_path))
    rt._reset_phase_for_reapply()  # the apply_session(reset=true) mechanism
    assert current_lifecycle(str(tmp_path)) == "applying"
    res = rt.resume_session()
    enrich_tool_response("apatch_resume_session", res, target_dir=str(tmp_path))
    # recovered to attestable, not flapped back to draft
    assert current_lifecycle(str(tmp_path)) == "verifying"


@pytest.mark.parametrize("blocked", [False, True])
def test_executor_restart_preserves_task_and_real_chunk_progress(tmp_path, monkeypatch, blocked):
    from apatch.apply_session import default_session_path, run_apply_session, save_session
    from apatch.runtime.session_binding import capture_session_binding
    from apatch.runtime.errors import SessionBindingError
    from tests.test_apply_session import _write_patch_log

    monkeypatch.delenv("APATCH_ENFORCE", raising=False)
    root = str(tmp_path)
    task = {"intent": "Update two files", "artifacts": [{"kind": "spec", "id": "SPEC-F08#R1"}]}
    save_session_state(root, {
        "session_id": "s-restart", **task, "phase": "apply",
        "session_token_hash": hash_session_token("old-process-token"),
    })
    for name in ("first.py", "second.py"):
        (tmp_path / name).write_text("value = 1\n")
    log = tmp_path / "patches.jsonl"
    _write_patch_log(log, [(name, "value = 1", "value = 2") for name in ("first.py", "second.py")])
    first = run_apply_session(str(log), root, chunk_max_files=1, no_trustchain=True, quiet=True)
    assert first["ok"] and first["continue"]
    enrich_tool_response("apatch_apply_session", first, target_dir=root)

    apply_path = Path(default_session_path(root))
    cursor = json.loads(apply_path.read_text())
    # no_trustchain omits the ledger ref; use the real physical backup created above.
    backup = next((tmp_path / ".apatch" / "backups").iterdir())
    checkpoint = backup.name
    cursor.update(last_checkpoint=checkpoint, checkpoints=[checkpoint])
    save_session(str(apply_path), cursor)
    raw = load_session_state(root)
    save_session_state(root, {**raw, "checkpoint": checkpoint})
    failure = {"error_type": "NOTARIZATION_FAILED", "message": "caller disconnected",
               "details": {"signing_request": {"request_id": "saved-request"}, "reconciliation_required": True}}
    if blocked:
        cursor["blocked_failure"] = {"ok": False, **failure, "checkpoint": checkpoint}
        save_session(str(apply_path), cursor)
        raw = load_session_state(root)
        save_session_state(root, {**raw, "phase": "blocked", "failure": failure})
    cursor_before = apply_path.read_bytes()
    files_before = [(tmp_path / name).read_bytes() for name in ("first.py", "second.py")]
    backups_before = {p.name: p.read_bytes() for p in backup.iterdir() if p.is_file()}
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    env.pop("APATCH_LANE", None)
    script = '''
import json, os, sys
from apatch.runtime.runtime import MutationRuntime
from apatch.session_state import enrich_tool_response
result = MutationRuntime(sys.argv[1]).resume_session()
result = enrich_tool_response("apatch_resume_session", result, target_dir=sys.argv[1])
print(json.dumps({"pid": os.getpid(), "result": result}))
'''
    tokens = []
    for _ in range(2):
        completed = subprocess.run([sys.executable, "-c", script, root], env=env,
                                   capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stderr
        observed = json.loads(completed.stdout)
        assert observed["pid"] != os.getpid()
        result = observed["result"]
        assert result["ok"] and result["resumed"]
        assert result["resume_mode"] == ("reconcile" if blocked else "apply")
        assert result["state_update"]["phase"] == ("blocked" if blocked else "apply")
        assert result["progress"]["chunks_done"] == 1
        assert result["progress"]["chunks_total"] == 2
        tokens.append(result["session_token"])
        state = load_session_state(root)
        assert {key: state[key] for key in task} == task
        assert state["checkpoint"] == checkpoint
        assert state["failure"] == (failure if blocked else None)
        assert apply_path.read_bytes() == cursor_before
        assert [(tmp_path / name).read_bytes() for name in ("first.py", "second.py")] == files_before
        assert {p.name: p.read_bytes() for p in backup.iterdir() if p.is_file()} == backups_before

    assert tokens[0] != tokens[1]
    if blocked:
        from apatch.runtime.errors import RuntimeTransitionError
        from apatch.runtime.state_machine import OP_VERIFY, assert_operation

        with pytest.raises(RuntimeTransitionError):
            assert_operation(root, OP_VERIFY, strict=True)
        assert load_session_state(root)["failure"] == failure
    with pytest.raises(SessionBindingError) as stale:
        capture_session_binding(root, session_token=tokens[0])
    assert stale.value.error_type == "SESSION_TOKEN_MISMATCH"
    capture_session_binding(root, session_token=tokens[1])
    if not blocked:
        second = run_apply_session(str(log), root, no_trustchain=True, quiet=True)
        assert second["ok"] and not second["continue"]
        assert second["progress"]["chunks_done"] == 2
        assert all((tmp_path / name).read_text() == "value = 2\n" for name in ("first.py", "second.py"))


@pytest.mark.parametrize("owner", [None, "another-session"])
def test_restart_does_not_adopt_unowned_or_foreign_progress(tmp_path, owner):
    from apatch.apply_session import default_session_path
    from apatch.session_state import session_state_path

    root = str(tmp_path)
    save_session_state(root, {"session_id": "s-owned", "intent": "keep this task", "phase": "apply"})
    state_path = Path(session_state_path(root))
    apply_path = Path(default_session_path(root))
    apply_path.write_text(json.dumps({"session_id": owner, "chunks": [[1], [2]], "chunk_index": 1}))
    before = state_path.read_bytes(), apply_path.read_bytes()

    result = MutationRuntime(root).resume_session()

    assert result["ok"] is False
    assert result["error_type"] == "SESSION_MISMATCH"
    assert (state_path.read_bytes(), apply_path.read_bytes()) == before
