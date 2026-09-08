"""Shared workflow layer (CLI + MCP)."""

import json
import os
import stat

from apatch.backup import BackupManager
from apatch.workflows import (
    apply_from_logs,
    filter_patch_candidates,
    plan_from_logs,
    rollback_workspace,
    run_strip,
    view_log_candidates,
)


def test_filter_patch_candidates_by_step(tmp_path):
    from apatch.ingestor import PatchCandidate

    cands = [
        PatchCandidate(1, "t", "a.py", "REPLACE", "x", "y", "fmt"),
        PatchCandidate(2, "t", "b.py", "REPLACE", "x", "y", "fmt"),
    ]
    out = filter_patch_candidates(cands, steps="2")
    assert len(out) == 1
    assert out[0].step_index == 2


def test_plan_from_logs(tmp_path):
    src = tmp_path / "main.py"
    src.write_text("old = 1\n", encoding="utf-8")
    log = tmp_path / "t.jsonl"
    log.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": "main.py",
                        "TargetContent": "old = 1",
                        "ReplacementContent": "old = 2",
                    },
                }],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = plan_from_logs(str(log), str(tmp_path))
    assert result["total"] == 1
    assert result["would_apply"] == 1


def test_plan_from_logs_chmod_has_mode_diff_not_content_diff(tmp_path):
    src = tmp_path / "run.sh"
    src.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    log = tmp_path / "chmod.jsonl"
    log.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [{
                    "name": "chmod_file",
                    "arguments": {"TargetFile": "run.sh", "Mode": "755"},
                }],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = plan_from_logs(str(log), str(tmp_path), show_diff=True)

    assert result["total"] == 1
    assert result["would_apply"] == 1
    entry = result["entries"][0]
    assert entry["action_type"] == "CHMOD"
    assert entry["strategy"] == "chmod"
    assert "mode" in entry["diff"]
    assert "---" not in entry["diff"]


def test_replace_preserves_executable_bit_and_chmod_changes_it_explicitly(tmp_path):
    source = tmp_path / "run.sh"
    source.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
    os.chmod(source, 0o755)
    replace_log = tmp_path / "replace.jsonl"
    replace_log.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": "run.sh",
                        "TargetContent": "echo old",
                        "ReplacementContent": "echo new",
                    },
                }],
            }
        ) + "\n",
        encoding="utf-8",
    )

    replaced = apply_from_logs(
        str(replace_log),
        str(tmp_path),
        no_trustchain=True,
        quiet=True,
    )
    assert replaced["ok"] is True
    assert stat.S_IMODE(source.stat().st_mode) == 0o755

    chmod_log = tmp_path / "chmod.jsonl"
    chmod_log.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [{
                    "name": "chmod_file",
                    "arguments": {"TargetFile": "run.sh", "Mode": "644"},
                }],
            }
        ) + "\n",
        encoding="utf-8",
    )
    changed = apply_from_logs(
        str(chmod_log),
        str(tmp_path),
        no_trustchain=True,
        quiet=True,
    )
    assert changed["ok"] is True
    assert stat.S_IMODE(source.stat().st_mode) == 0o644


def test_rollback_workspace_files(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("original\n", encoding="utf-8")
    mgr = BackupManager(str(tmp_path), session_id="wf_test")
    mgr.create_backup(str(target))
    target.write_text("mutated\n", encoding="utf-8")

    result = rollback_workspace(str(tmp_path), "wf_test")
    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "original\n"


def test_view_log_candidates(tmp_path):
    log = tmp_path / "v.jsonl"
    log.write_text(
        json.dumps(
            {
                "step_index": 3,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": "x.py",
                        "TargetContent": "a",
                        "ReplacementContent": "b",
                    },
                }],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = view_log_candidates(str(log))
    assert rows[0]["step_index"] == 3


def test_run_strip_dry_run(tmp_path):
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    page = tmp_path / "P.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")
    result = run_strip(
        str(page),
        manifest_path=str(manifest),
        dry_run=True,
        out_dir=str(tmp_path / "out"),
        strict_overlap=True,
    )
    assert result["ok"] is True
    assert result["exported_meta"]
