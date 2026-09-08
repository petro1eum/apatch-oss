import json

import pytest

from apatch.simulate import simulate_from_logs
from apatch.workflows import WorkflowError, simulate_workspace


def _write_log(path, n=3):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            (path.parent / f"f{i}.py").write_text(f"x{i} = 1\n", encoding="utf-8")
            step = {
                "step_index": i + 1,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": f"f{i}.py",
                        "TargetContent": f"x{i} = 1",
                        "ReplacementContent": f"x{i} = 2",
                    },
                }],
            }
            f.write(json.dumps(step) + "\n")


def test_simulate_from_logs_basic(tmp_path):
    log = tmp_path / "p.jsonl"
    _write_log(log, n=4)
    result = simulate_from_logs(str(log), str(tmp_path), chunk_max_files=2)
    assert result["ok"] is True
    assert result["dry_run"] is True
    rm = result["risk_map"]
    assert rm["files_touched"] == 4
    assert rm["chunks_required"] == 2
    assert 0 <= result["rollback_probability"] <= 1
    assert result["execution_graph"]["nodes"]
    assert result["recommended_path"] in ("apatch_apply", "apatch_apply_session")


def test_simulate_recommends_session_for_large_batch(tmp_path):
    log = tmp_path / "big.jsonl"
    _write_log(log, n=20)
    result = simulate_from_logs(str(log), str(tmp_path))
    assert result["recommended_path"] == "apatch_apply_session"
    assert result["risk_map"]["chunks_required"] >= 4


def test_simulate_workspace_manifest(tmp_path):
    log = tmp_path / "patches.jsonl"
    _write_log(log, n=2)
    manifest = tmp_path / "pipe.json"
    manifest.write_text(
        json.dumps({
            "kind": "engineering-pipeline",
            "patches_jsonl": "patches.jsonl",
            "db_profile": "sqlalchemy",
            "phases": [{"name": "plan", "action": "plan"}, {"name": "apply", "action": "apply"}],
        }),
        encoding="utf-8",
    )
    result = simulate_workspace(str(tmp_path), manifest_path=str(manifest))
    assert result["ok"] is True
    assert "apply_session" in result["execution_graph"]["nodes"] or "plan" in result["execution_graph"]["nodes"]


def test_simulate_missing_logs(tmp_path):
    with pytest.raises(WorkflowError):
        simulate_workspace(str(tmp_path))
