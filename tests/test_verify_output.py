"""SPEC-APPLY-OBS-1#R2: failed verify output must propagate to MCP callers."""

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


def test_verify_failure_output_propagates(tmp_path):
    """In MCP stdio mode the TUI console writes to /dev/null; the failed
    verify stdout/stderr must still reach the caller via verify_output."""
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    _write_patch_log(log, [("b.py", "b = 1", "b = 2")])

    r = run_apply_session(
        str(log),
        str(tmp_path),
        verify="echo BOOM_MARKER; exit 1",
        no_trustchain=True,
        reset=True,
        quiet=True,
    )
    assert r["ok"] is False
    assert "BOOM_MARKER" in r.get("verify_output", "")