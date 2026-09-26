"""The runtime Git hygiene operation never deletes working-tree files."""

from __future__ import annotations

import subprocess
from pathlib import Path

from apatch.git_hygiene import RUNTIME_PATHS, untrack_runtime_files_workspace


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, *, ignore_all: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    for rel in RUNTIME_PATHS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{rel}: initial\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        "\n".join(RUNTIME_PATHS if ignore_all else RUNTIME_PATHS[:3]) + "\n",
        encoding="utf-8",
    )
    _git(root, "add", "-f", ".apatch")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-qm", "baseline")
    return root


def test_runtime_untrack_preserves_files_and_commits_only_exact_deletions(tmp_path):
    root = _repo(tmp_path)
    before = {}
    for rel in RUNTIME_PATHS:
        path = root / rel
        path.write_text(f"{rel}: live\n", encoding="utf-8")
        before[rel] = path.read_bytes()
    (root / "unrelated.txt").write_text("user work\n", encoding="utf-8")

    preview = untrack_runtime_files_workspace(str(root), dry_run=True)
    assert preview["ok"] is True
    assert preview["paths"] == list(RUNTIME_PATHS)
    assert _git(root, "ls-files", *RUNTIME_PATHS).splitlines() == sorted(RUNTIME_PATHS)

    result = untrack_runtime_files_workspace(str(root))
    assert result["ok"] is True, result
    assert result["committed"] is True
    assert _git(root, "show", "--name-only", "--format=", "HEAD").splitlines() == sorted(RUNTIME_PATHS)
    assert _git(root, "ls-files", *RUNTIME_PATHS) == ""
    assert _git(root, "diff", "--cached", "--name-only") == ""
    assert (root / "unrelated.txt").read_text() == "user work\n"
    for rel in before:
        assert (root / rel).is_file()
        assert (root / rel).stat().st_size > 0
    assert before[".apatch/events.jsonl"] in (root / ".apatch/events.jsonl").read_bytes()


def test_runtime_untrack_refuses_missing_ignore_and_dirty_index(tmp_path):
    root = _repo(tmp_path, ignore_all=False)
    missing = untrack_runtime_files_workspace(str(root), dry_run=True)
    assert missing["error_type"] == "RUNTIME_IGNORE_MISSING"
    assert _git(root, "ls-files", *RUNTIME_PATHS).splitlines() == sorted(RUNTIME_PATHS)

    (root / "other.txt").write_text("staged\n")
    _git(root, "add", "other.txt")
    dirty = untrack_runtime_files_workspace(str(root))
    assert dirty["error_type"] == "GIT_INDEX_NOT_CLEAN"
    assert _git(root, "diff", "--cached", "--name-only") == "other.txt"
