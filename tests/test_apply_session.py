import json
import os
import stat

import pytest

from apatch.backup import BackupManager
from apatch.apply_session import (
    MASS_APPLY_GUARD,
    plan_chunks_for_logs,
    run_apply_session,
    should_require_session,
)
from apatch.workflows import WorkflowError


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


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_should_require_session_guard():
    assert should_require_session(MASS_APPLY_GUARD + 1) is True
    assert should_require_session(5) is False
    assert should_require_session(100, steps="1,2") is False
    assert should_require_session(100, budget={"max_files": 5}) is False


def test_plan_chunks_groups_by_files(tmp_path):
    for i in range(3):
        (tmp_path / f"f{i}.py").write_text(f"v{i} = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    _write_patch_log(
        log,
        [("f0.py", "v0 = 1", "v0 = 2"), ("f1.py", "v1 = 1", "v1 = 2"), ("f2.py", "v2 = 1", "v2 = 2")],
    )
    layout = plan_chunks_for_logs(str(log), str(tmp_path), chunk_max_files=2)
    assert layout["chunk_count"] == 2
    assert sum(len(c) for c in layout["chunks"]) == 3


def test_plan_chunks_keeps_one_file_atomic(tmp_path):
    target = tmp_path / "large.json"
    target.write_text(
        "\n".join(f"k{i}=old" for i in range(6)) + "\n",
        encoding="utf-8",
    )
    log = tmp_path / "same-file.jsonl"
    _write_patch_log(
        log,
        [
            ("large.json", f"k{i}=old", f"k{i}=new")
            for i in range(6)
        ],
    )

    layout = plan_chunks_for_logs(str(log), str(tmp_path), chunk_max_files=5)

    assert layout["chunks"] == [[1, 2, 3, 4, 5, 6]]
    result = run_apply_session(
        str(log),
        str(tmp_path),
        chunk_max_files=5,
        no_trustchain=True,
        reset=True,
        quiet=True,
    )
    assert result["continue"] is False
    assert result["chunk_result"]["applied"] == 6
    assert all(
        f"k{i}=new" in target.read_text(encoding="utf-8")
        for i in range(6)
    )


def test_plan_chunks_preserves_order_for_noncontiguous_file_steps(tmp_path):
    (tmp_path / "a.py").write_text("a1=old\na2=old\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b1=old\n", encoding="utf-8")
    log = tmp_path / "interleaved.jsonl"
    _write_patch_log(
        log,
        [
            ("a.py", "a1=old", "a1=new"),
            ("b.py", "b1=old", "b1=new"),
            ("a.py", "a2=old", "a2=new"),
        ],
    )

    layout = plan_chunks_for_logs(str(log), str(tmp_path), chunk_max_files=1)

    # The A interval contains step 2, so the whole ordered range is atomic.
    assert layout["chunks"] == [[1, 2, 3]]


def test_apply_session_human_output(tmp_path):
    from click.testing import CliRunner

    from apatch.cli import cli

    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    import json

    rec = {
        "step_index": 1,
        "tool_calls": [{
            "name": "replace_file_content",
            "arguments": {
                "TargetFile": "a.py",
                "TargetContent": "x = 1",
                "ReplacementContent": "x = 2",
            },
        }],
    }
    log.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "apply-session",
            "--logs",
            str(log),
            "--target-dir",
            str(tmp_path),
            "--no-trustchain",
            "--reset",
        ],
    )
    assert result.exit_code == 0
    assert "chunk" in result.output.lower()
    assert '"ok"' not in result.output


def test_apply_session_chunked_with_checkpoint(tmp_path):
    files = []
    steps = []
    for i in range(4):
        name = f"m{i}.py"
        (tmp_path / name).write_text(f"x{i} = 1\n", encoding="utf-8")
        files.append(name)
        steps.append((name, f"x{i} = 1", f"x{i} = 2"))
    log = tmp_path / "p.jsonl"
    _write_patch_log(log, steps)

    r1 = run_apply_session(
        str(log),
        str(tmp_path),
        chunk_max_files=2,
        no_trustchain=True,
        reset=True,
        quiet=True,
    )
    assert r1["continue"] is True
    assert r1["progress"]["chunks_done"] == 1
    assert (tmp_path / "m0.py").read_text(encoding="utf-8") == "x0 = 2\n"

    r2 = run_apply_session(str(log), str(tmp_path), chunk_max_files=2, no_trustchain=True, quiet=True)
    assert r2["continue"] is False
    assert r2["phase"] == "done"
    assert (tmp_path / "m3.py").read_text(encoding="utf-8") == "x3 = 2\n"


def test_apply_session_chmod_and_rollback(tmp_path):
    from apatch.workflows import generate_patch_jsonl_batch

    script = tmp_path / "scripts" / "run_live_feedback_contract.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o644)
    log = tmp_path / "chmod.jsonl"
    gen = generate_patch_jsonl_batch(
        needles=[
            {
                "action": "chmod",
                "target_file": "scripts/run_live_feedback_contract.sh",
                "executable": True,
            }
        ],
        target_dir=str(tmp_path),
        out_path=str(log),
    )
    assert gen["ok"] is True

    result = run_apply_session(
        str(log),
        str(tmp_path),
        chunk_max_files=1,
        no_trustchain=True,
        reset=True,
        quiet=True,
    )

    assert result["ok"] is True
    assert result["chunk_result"]["applied"] == 1
    assert _mode(script) == 0o755
    report_path = result["chunk_result"]["report_path"]
    report = json.loads(open(report_path, encoding="utf-8").read())
    assert report["entries"][0]["action_type"] == "CHMOD"

    sessions = BackupManager.get_available_sessions(str(tmp_path))
    assert sessions
    BackupManager.rollback_session(str(tmp_path), sessions[0][0])
    assert _mode(script) == 0o644


def test_apply_session_abort_clears_state(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    _write_patch_log(log, [("a.py", "a = 1", "a = 2")])
    run_apply_session(str(log), str(tmp_path), chunk_max_files=5, no_trustchain=True, reset=True)
    session_file = tmp_path / ".apatch" / "apply_session.json"
    assert session_file.exists()
    result = run_apply_session(str(log), str(tmp_path), abort=True, no_trustchain=True)
    assert result["aborted"] is True
    assert not session_file.exists()


def test_apply_session_logs_path_mismatch_in_progress(tmp_path):
    for i in range(3):
        name = f"f{i}.py"
        (tmp_path / name).write_text(f"v{i} = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    _write_patch_log(
        log,
        [(f"f{i}.py", f"v{i} = 1", f"v{i} = 2") for i in range(3)],
    )
    r1 = run_apply_session(
        str(log), str(tmp_path), chunk_max_files=2, no_trustchain=True, reset=True, quiet=True
    )
    assert r1["continue"] is True
    other = tmp_path / "other.jsonl"
    _write_patch_log(other, [("f0.py", "v0 = 2", "v0 = 3")])
    with pytest.raises(WorkflowError, match="in-progress session"):
        run_apply_session(str(other), str(tmp_path), no_trustchain=True)


def test_apply_session_completed_clears_stale_for_new_logs(tmp_path):
    log = tmp_path / "p.jsonl"
    _write_patch_log(log, [("a.py", "a = 1", "a = 2")])
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    done = run_apply_session(str(log), str(tmp_path), no_trustchain=True, reset=True, quiet=True)
    assert done["continue"] is False
    session_file = tmp_path / ".apatch" / "apply_session.json"
    assert session_file.exists()

    other = tmp_path / "other.jsonl"
    _write_patch_log(other, [("a.py", "a = 2", "a = 3")])
    r2 = run_apply_session(str(other), str(tmp_path), no_trustchain=True, reset=False, quiet=True)
    assert r2["ok"] is True
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 3\n"


def test_apply_session_logs_path_normalized_relative_absolute(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    _write_patch_log(log, [("a.py", "a = 1", "a = 2")])
    run_apply_session(str(log), str(tmp_path), chunk_max_files=5, no_trustchain=True, reset=True, quiet=True)
    r2 = run_apply_session(
        str(log.resolve()),
        str(tmp_path),
        chunk_max_files=5,
        no_trustchain=True,
        quiet=True,
    )
    assert r2["continue"] is False
    assert r2["phase"] == "done"
