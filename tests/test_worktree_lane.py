"""Tests for the general worktree-lane entry point (#6 part 3 — parallel agents)."""
import os
import subprocess

import pytest


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path):
    path.mkdir()
    _git(["init", "-q"], str(path))
    _git(["config", "user.email", "t@t"], str(path))
    _git(["config", "user.name", "t"], str(path))
    (path / "f.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], str(path))
    _git(["commit", "-qm", "init"], str(path))


def test_create_and_remove_worktree_lane(tmp_path):
    main = tmp_path / "repo"
    _init_repo(main)
    lanes = str(tmp_path / "lanes")

    from apatch.worktree_lane import create_worktree_lane, remove_worktree_lane

    res = create_worktree_lane(str(main), "agent-2", lanes_parent=lanes)
    # a real, isolated git worktree: own working tree + branch + bootstrapped .apatch
    assert os.path.isdir(res["path"])
    assert res["branch"] == "work/agent-2"
    assert os.path.isfile(os.path.join(res["path"], "f.txt"))
    assert os.path.isfile(os.path.join(res["path"], ".apatch", "lane.json"))

    # idempotent: a second call returns the same worktree, not a duplicate
    res2 = create_worktree_lane(str(main), "agent-2", lanes_parent=lanes)
    assert res2["path"] == res["path"]

    # removable (branch is kept; only the worktree dir goes away)
    assert remove_worktree_lane(str(main), "agent-2", lanes_parent=lanes, force=True)
    assert not os.path.isdir(res["path"])
