import os
import shutil
import subprocess

import pytest

from apatch.match_session import MatchSession
from apatch.path_index import PathIndex, iter_target_files
from apatch.resolver import resolve_smart_path


def test_path_index_basename_lookup(tmp_path):
    target = tmp_path / "project"
    (target / "nested").mkdir(parents=True)
    file_a = target / "nested" / "widget.py"
    file_b = target / "other" / "widget.py"
    file_b.parent.mkdir()
    file_a.write_text("a")
    file_b.write_text("b")

    index = PathIndex.build(str(target))
    assert index.source == "walk"
    matches = index.resolve_basename("widget.py")
    assert len(matches) == 2
    assert all(m.startswith(str(target)) for m in matches)


def test_resolve_with_index_skips_os_walk(tmp_path, monkeypatch):
    target = tmp_path / "project"
    (target / "deep").mkdir(parents=True)
    found = target / "deep" / "lost.cpp"
    found.write_text("int main() { return 0; }")

    index = PathIndex.build(str(target))
    walk_calls = {"n": 0}
    real_walk = os.walk

    def counting_walk(top, *args, **kwargs):
        walk_calls["n"] += 1
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", counting_walk)

    r1 = resolve_smart_path(str(target), "/tmp/nowhere/lost.cpp", index=index)
    r2 = resolve_smart_path(str(target), "/var/x/lost.cpp", index=index)

    assert r1 == os.path.abspath(found)
    assert r2 == os.path.abspath(found)
    assert walk_calls["n"] == 0


def test_prune_skips_git_directory(tmp_path):
    target = tmp_path / "project"
    git_objects = target / ".git" / "objects" / "ab"
    src = target / "src"
    git_objects.mkdir(parents=True)
    src.mkdir()
    (git_objects / "secret.bin").write_bytes(b"x")
    (src / "visible.py").write_text("ok")

    paths = list(iter_target_files(str(target)))
    assert len(paths) == 1
    assert paths[0].endswith("visible.py")
    assert not any(".git" in p for p in paths)


def test_path_index_from_git(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not installed")

    target = tmp_path / "repo"
    target.mkdir()
    subprocess.run(["git", "init"], cwd=target, check=True, capture_output=True)
    (target / "pkg").mkdir()
    tracked = target / "pkg" / "module.py"
    tracked.write_text("x = 1")
    subprocess.run(["git", "add", "pkg/module.py"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=target,
        check=True,
        capture_output=True,
    )

    index = PathIndex.build(str(target))
    assert index.source == "git"
    assert os.path.abspath(tracked) in index.resolve_basename("module.py")


def test_match_session_reuses_matcher(tmp_path):
    target = tmp_path / "file.py"
    target.write_text("a = 1\n")

    session = MatchSession()
    m1 = session.matcher_for(str(target))
    m2 = session.matcher_for(str(target))
    assert m1 is m2

    session.invalidate(str(target))
    m3 = session.matcher_for(str(target))
    assert m3 is not m1


def test_resolve_backward_compat_without_index(tmp_path):
    target = tmp_path / "project"
    (target / "x").mkdir(parents=True)
    file_path = target / "x" / "only_name.rs"
    file_path.write_text("fn main() {}")

    resolved = resolve_smart_path(str(target), "/wrong/path/only_name.rs")
    assert resolved == os.path.abspath(file_path)
