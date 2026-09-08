import json

import pytest

from apatch.enforcement import (
    file_sha256,
    is_enforcement_enabled,
    record_notarized_files,
    verify_paths_notarized,
    write_enforcement_config,
)
from apatch.workflows import WorkflowError, apply_from_logs


def test_enforcement_blocks_no_trustchain(tmp_path):
    write_enforcement_config(str(tmp_path))
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    step = {
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
    log.write_text(json.dumps(step) + "\n", encoding="utf-8")
    with pytest.raises(WorkflowError, match="ОТКЛОНЕНО"):
        apply_from_logs(str(log), str(tmp_path), no_trustchain=True, quiet=True)


def test_notarized_index_blocks_unproven_change(tmp_path):
    write_enforcement_config(str(tmp_path))
    src = tmp_path / "svc.py"
    src.write_text("a = 1\n", encoding="utf-8")
    good_hash = file_sha256(str(src))
    record_notarized_files(str(tmp_path), {"svc.py": {"sha256": good_hash}})
    src.write_text("a = 2\n", encoding="utf-8")
    result = verify_paths_notarized(str(tmp_path), ["svc.py"])
    assert result["ok"] is False
    assert result["violations"][0]["reason"] == "sha256_mismatch"


def test_never_notarized_file(tmp_path):
    write_enforcement_config(str(tmp_path))
    rogue = tmp_path / "rogue.py"
    rogue.write_text("hack = True\n", encoding="utf-8")
    result = verify_paths_notarized(str(tmp_path), ["rogue.py"])
    assert result["violations"][0]["reason"] == "never_notarized"


def test_enforcement_env_override(tmp_path, monkeypatch):
    assert not is_enforcement_enabled(str(tmp_path))
    monkeypatch.setenv("APATCH_ENFORCE", "1")
    assert is_enforcement_enabled(str(tmp_path))
