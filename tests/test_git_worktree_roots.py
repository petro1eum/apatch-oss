from __future__ import annotations

from apatch.git_util import find_git_root as find_git_util_root
from apatch.path_index import find_git_root as find_path_index_root
from apatch.trustchain_helper import TrustChainHelper


def test_linked_worktree_gitfile_is_the_exact_workspace_root(tmp_path):
    root = tmp_path / "linked-worktree"
    nested = root / "apatch" / "remote"
    nested.mkdir(parents=True)
    (root / ".git").write_text("gitdir: /tmp/example/worktrees/release\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'example'\n", encoding="utf-8")

    expected = str(root.resolve())
    assert TrustChainHelper.resolve_workspace_root(str(nested)) == expected
    assert find_git_util_root(str(nested)) == expected
    assert find_path_index_root(str(nested)) == expected


def test_trustchain_discovery_does_not_escape_the_worktree_root(tmp_path):
    parent_ledger = tmp_path / ".trustchain"
    parent_ledger.mkdir()
    root = tmp_path / "linked-worktree"
    root.mkdir()
    (root / ".git").write_text("gitdir: /tmp/example/worktrees/release\n", encoding="utf-8")

    helper = TrustChainHelper(str(root), auto_init=False)

    assert helper.workspace_root == str(root.resolve())
    assert helper.trustchain_dir is None


def test_trustchain_discovery_uses_a_workspace_local_ledger(tmp_path):
    root = tmp_path / "ordinary-clone"
    (root / ".git").mkdir(parents=True)
    local_ledger = root / ".trustchain"
    local_ledger.mkdir()

    helper = TrustChainHelper(str(root), auto_init=False)

    assert helper.trustchain_dir == str(local_ledger.resolve())


def test_git_directory_remains_a_workspace_root(tmp_path):
    root = tmp_path / "ordinary-clone"
    nested = root / "src"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()

    expected = str(root.resolve())
    assert TrustChainHelper.resolve_workspace_root(str(nested)) == expected
    assert find_git_util_root(str(nested)) == expected
    assert find_path_index_root(str(nested)) == expected
