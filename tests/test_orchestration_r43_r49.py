"""Tests for R43 db run and R49 change budget."""

import json
import subprocess

import pytest

from apatch.change_budget import check_budget, estimate_from_candidates, resolve_budget
from apatch.ingestor import PatchCandidate
from apatch.workflows import apply_from_logs, db_run_manifest


def _git_init(root):
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)


def _git_commit(root, msg="init"):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True, capture_output=True)


def test_resolve_budget_preset():
    b = resolve_budget("small")
    assert b["max_files"] == 5


def test_change_budget_blocks_apply(tmp_path):
    src = tmp_path / "a.py"
    src.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    log.write_text(
        json.dumps({
            "step_index": 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "a.py",
                    "TargetContent": "x = 1\ny = 2\nz = 3\n",
                    "ReplacementContent": "x = 9\n" + "y = 2\n" * 30,
                },
            }],
        }) + "\n",
        encoding="utf-8",
    )
    result = apply_from_logs(
        str(log),
        str(tmp_path),
        change_budget={"max_files": 1, "max_insertions": 2, "max_deletions": 2},
        no_trustchain=True,
    )
    assert result["ok"] is False
    assert result["reason"] == "change_budget_exceeded"


def test_db_run_dry_run(git_project):
    patches = git_project / "patches"
    patches.mkdir()
    (git_project / "app").mkdir()
    (git_project / "app" / "db_models.py").write_text("class User: pass\n", encoding="utf-8")
    plog = patches / "refactor.jsonl"
    plog.write_text(
        json.dumps({
            "step_index": 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "app/db_models.py",
                    "TargetContent": "class User: pass",
                    "ReplacementContent": "class User: pass\nclass Order: pass",
                },
            }],
        }) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "kind": "db-refactor",
        "profile": "sqlalchemy",
        "patches_jsonl": "patches/refactor.jsonl",
        "phases": [
            {"name": "apply", "action": "apply", "no_trustchain": True},
            {"name": "check", "action": "db_check"},
            {"name": "verify", "action": "shell", "command": "true"},
        ],
    }
    mpath = git_project / "manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    _git_commit(git_project)

    result = db_run_manifest(str(mpath), str(git_project), dry_run=True)
    assert result["ok"] is True
    assert len(result["phases"]) == 3
    assert result["phases"][0]["action"] == "apply"
    assert result["phases"][0].get("dry_run") is True


def test_db_run_fails_on_missing_migration(git_project):
    patches = git_project / "patches"
    patches.mkdir()
    (git_project / "app").mkdir()
    (git_project / "app" / "db_models.py").write_text("class User: pass\n", encoding="utf-8")
    plog = patches / "refactor.jsonl"
    plog.write_text(
        json.dumps({
            "step_index": 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "app/db_models.py",
                    "TargetContent": "class User: pass",
                    "ReplacementContent": "class User: pass\nclass Order: pass",
                },
            }],
        }) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "kind": "db-refactor",
        "profile": "sqlalchemy",
        "patches_jsonl": "patches/refactor.jsonl",
        "phases": [
            {
                "name": "apply",
                "action": "apply",
                "no_trustchain": True,
                "verify": "true",
                "verify_deferred": True,
            },
            {"name": "check", "action": "db_check"},
        ],
    }
    mpath = git_project / "manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    _git_commit(git_project)

    result = db_run_manifest(str(mpath), str(git_project), dry_run=False)
    assert result["ok"] is False
    assert result["failed_phase"] == "check"


@pytest.fixture
def git_project(tmp_path):
    _git_init(tmp_path)
    yield tmp_path
