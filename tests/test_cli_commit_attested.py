from __future__ import annotations

import json

from click.testing import CliRunner

from apatch.cli import cli


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
