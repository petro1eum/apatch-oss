"""SPEC-APPLY-OBS-1#R1: apply_session must not no-op resume after verify rollback."""

import json

from apatch.apply_session import run_apply_session


def _write_patch_log(path, steps):
    with open(path, "w", encoding="utf-8") as f:
        for i, (fname, old, new) in enumerate(steps, start=1):
            rec = {
                "step_index": i,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": fname,
                        "TargetContent": old,
                        "ReplacementContent": new,
                    },
                }],
            }
            f.write(json.dumps(rec) + "\n")


def test_no_noop_resume_after_verify_rollback(tmp_path):
    """A verify rollback must clear the session so the next call re-applies.

    Before the fix, chunk_index advanced even when the chunk was rolled back;
    the next call resumed a 'completed' session and silently applied nothing
    while reporting ok=true.
    """
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    _write_patch_log(log, [("a.py", "a = 1", "a = 2")])

    r1 = run_apply_session(
        str(log),
        str(tmp_path),
        verify="exit 1",
        no_trustchain=True,
        reset=True,
        quiet=True,
    )
    assert r1["ok"] is False
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert not (tmp_path / ".apatch" / "apply_session.json").exists()

    r2 = run_apply_session(
        str(log),
        str(tmp_path),
        verify="exit 0",
        no_trustchain=True,
        quiet=True,
    )
    assert r2["ok"] is True
    assert r2["chunk_result"]["applied"] == 1
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 2\n"