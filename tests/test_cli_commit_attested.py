from __future__ import annotations

import json

from click.testing import CliRunner

from apatch.cli import cli


def test_git_untrack_runtime_cli_is_exact_and_preview_only(tmp_path, monkeypatch):
    captured = {}

    def fake_untrack(target_dir, **kwargs):
        captured["target_dir"] = target_dir
        captured.update(kwargs)
        return {"ok": True, "dry_run": True, "paths": [".apatch/events.jsonl"]}

    monkeypatch.setattr("apatch.workflows.git_untrack_runtime_workspace", fake_untrack)
    result = CliRunner().invoke(
        cli, ["git-untrack-runtime", "--target-dir", str(tmp_path), "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is True
    assert captured == {
        "target_dir": str(tmp_path),
        "message": "Stop tracking APatch runtime files",
        "dry_run": True,
    }


def test_commit_attested_cli_passes_exact_sessions_and_dry_run(tmp_path, monkeypatch):
    captured = {}

    def fake_commit(target_dir, **kwargs):
        captured["target_dir"] = target_dir
        captured.update(kwargs)
        return {
            "ok": True,
            "committed": False,
            "status": "validated",
            "files": ["a.py"],
        }

    monkeypatch.setattr("apatch.workflows.commit_attested_workspace", fake_commit)
    result = CliRunner().invoke(
        cli,
        [
            "commit-attested",
            "--target-dir",
            str(tmp_path),
            "--session",
            "session-a",
            "--session",
            "session-b",
            "-m",
            "Exact proof",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "validated"
    assert captured == {
        "target_dir": str(tmp_path),
        "governed_session_id": None,
        "session_ids": ["session-a", "session-b"],
        "message": "Exact proof",
        "push": False,
        "remote": "origin",
        "dry_run": True,
    }
