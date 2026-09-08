from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from apatch import git_commit


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "a.txt").write_text("base a\n", encoding="utf-8")
    (root / "b.txt").write_text("base b\n", encoding="utf-8")
    _git(root, "add", "a.txt", "b.txt")
    _git(root, "commit", "-qm", "baseline")
    return root


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows(session_id: str, path: str, sha256: str):
    return [
        {
            "id": "op_1",
            "tool_id": "apatch",
            "timestamp": "2026-08-31T10:00:00+00:00",
            "signature": "signed-mutation",
            "payload": {
                "action": "chunk",
                "governed_session_id": session_id,
                "files": {path: {"sha256": sha256}},
            },
        },
        {
            "id": "op_2",
            "tool_id": "apatch_attest",
            "timestamp": "2026-08-31T10:01:00+00:00",
            "signature": "signed-attestation",
            "payload": {
                "governed_session_id": session_id,
                "session_id": session_id,
            },
        },
    ]


def _proof_ok(_root: str, paths):
    return {"ok": True, "checked": len(paths), "violations": []}


def test_commit_attested_prefers_rich_duplicate_ledger_object(
    tmp_path, monkeypatch
):
    root = _repo(tmp_path)
    (root / "a.txt").write_text("attested a\n", encoding="utf-8")
    rows = _rows("session-a", "a.txt", _sha("attested a\n"))
    for index, row in enumerate(rows):
        row["governed_session_id"] = "session-a"
        row["payload"].pop("governed_session_id", None)
        row["payload"]["session_id"] = f"checkpoint-{index}"
    rows.insert(
        0,
        {
            "id": "op_1",
            "tool_id": "apatch",
            "timestamp": "2026-08-31T10:00:00+00:00",
            "signature": "summary-envelope",
            "payload": {"action": "chunk"},
        },
    )
    monkeypatch.setattr(git_commit, "_ledger_entries", lambda _root: rows)
    monkeypatch.setattr(git_commit, "verify_paths_notarized", _proof_ok)

    result = git_commit.commit_attested_workspace(
        str(root), session_ids=["session-a"], message="Legacy envelope", dry_run=True
    )

    assert result["ok"] is True
    assert result["files"] == ["a.txt"]


def test_commit_attested_commits_only_selected_files_and_leaves_other_dirty(
    tmp_path, monkeypatch
):
    root = _repo(tmp_path)
    (root / "a.txt").write_text("attested a\n", encoding="utf-8")
    (root / "b.txt").write_text("unrelated b\n", encoding="utf-8")
    monkeypatch.setattr(
        git_commit,
        "_ledger_entries",
        lambda _root: _rows("session-a", "a.txt", _sha("attested a\n")),
    )
    monkeypatch.setattr(git_commit, "verify_paths_notarized", _proof_ok)
    result = git_commit.commit_attested_workspace(
        str(root), session_ids=["session-a"], message="Commit exact attested file"
    )
    assert result["ok"] is True
    assert result["committed"] is True
    assert result["files"] == ["a.txt"]
    assert _git(root, "show", "--name-only", "--format=", "HEAD") == "a.txt"
    assert _git(root, "diff", "--name-only") == "b.txt"
    assert _git(root, "diff", "--cached", "--name-only") == ""


def test_commit_attested_rejects_drift_without_staging(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / "a.txt").write_text("newer drift\n", encoding="utf-8")
    monkeypatch.setattr(
        git_commit,
        "_ledger_entries",
        lambda _root: _rows("session-a", "a.txt", _sha("attested a\n")),
    )
    monkeypatch.setattr(git_commit, "verify_paths_notarized", _proof_ok)
    result = git_commit.commit_attested_workspace(
        str(root), session_ids=["session-a"], message="Must fail"
    )
    assert result["ok"] is False
    assert result["error_type"] == "ATTESTED_FILE_DRIFT"
    assert _git(root, "diff", "--cached", "--name-only") == ""


def test_commit_attested_rejects_preexisting_staged_files(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / "a.txt").write_text("attested a\n", encoding="utf-8")
    (root / "b.txt").write_text("staged b\n", encoding="utf-8")
    _git(root, "add", "b.txt")
    monkeypatch.setattr(
        git_commit,
        "_ledger_entries",
        lambda _root: _rows("session-a", "a.txt", _sha("attested a\n")),
    )
    result = git_commit.commit_attested_workspace(
        str(root), session_ids=["session-a"], message="Must fail"
    )
    assert result["ok"] is False
    assert result["error_type"] == "GIT_INDEX_NOT_CLEAN"
    assert _git(root, "diff", "--cached", "--name-only") == "b.txt"


def test_commit_attested_requires_later_signed_attestation(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / "a.txt").write_text("attested a\n", encoding="utf-8")
    rows = _rows("session-a", "a.txt", _sha("attested a\n"))[:1]
    monkeypatch.setattr(git_commit, "_ledger_entries", lambda _root: rows)
    result = git_commit.commit_attested_workspace(
        str(root), session_ids=["session-a"], message="Must fail"
    )
    assert result["ok"] is False
    assert result["error_type"] == "ATTESTATION_MISSING"


def test_commit_attested_dry_run_validates_without_staging(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    before = _git(root, "rev-parse", "HEAD")
    (root / "a.txt").write_text("attested a\n", encoding="utf-8")
    monkeypatch.setattr(
        git_commit,
        "_ledger_entries",
        lambda _root: _rows("session-a", "a.txt", _sha("attested a\n")),
    )
    monkeypatch.setattr(git_commit, "verify_paths_notarized", _proof_ok)
    result = git_commit.commit_attested_workspace(
        str(root), session_ids=["session-a"], message="Dry run", dry_run=True
    )
    assert result["ok"] is True
    assert result["status"] == "validated"
    assert result["committed"] is False
    assert _git(root, "rev-parse", "HEAD") == before
    assert _git(root, "diff", "--cached", "--name-only") == ""


def test_commit_attested_can_push_new_branch_to_configured_remote(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    _git(root, "remote", "add", "origin", str(remote))
    (root / "a.txt").write_text("attested a\n", encoding="utf-8")
    monkeypatch.setattr(
        git_commit,
        "_ledger_entries",
        lambda _root: _rows("session-a", "a.txt", _sha("attested a\n")),
    )
    monkeypatch.setattr(git_commit, "verify_paths_notarized", _proof_ok)
    result = git_commit.commit_attested_workspace(
        str(root),
        session_ids=["session-a"],
        message="Push exact attested file",
        push=True,
    )
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    remote_head = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert result["ok"] is True
    assert result["pushed"] is True
    assert remote_head == result["commit"]
