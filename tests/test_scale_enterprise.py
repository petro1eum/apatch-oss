"""Integration tests for ADR-001 enterprise scale features (SCALE-1 … SCALE-8)."""
from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apatch.ast_window import find_parse_anchor
from apatch.match_session import MatchSession
from apatch.matcher import ASTMatcher
from apatch.path_index import PathIndex
from apatch.plan_worker import evaluate_plan_entry, parallel_plan_evaluate
from apatch.resolver import resolve_smart_path
from apatch.scale_config import get_ast_window_config, get_plan_workers, resolve_path_backend
from apatch.trustchain_helper import TrustChainHelper


# ---------------------------------------------------------------------------
# SCALE-5 — windowed AST
# ---------------------------------------------------------------------------


def test_ast_window_tiny_window_falls_back_to_full_parse(tmp_path, monkeypatch):
    """Window too small to parse → full-file fallback still applies patch."""
    monkeypatch.setenv("APATCH_AST_WINDOW_BYTES", "64")
    monkeypatch.setenv("APATCH_AST_FULL_PARSE_MAX", "512")

    padding = "pass\n" * 120
    fn = "def fallback_fn():\n    return 1\n"
    path = tmp_path / "big.py"
    path.write_text(padding + fn, encoding="utf-8")
    assert len(path.read_text(encoding="utf-8")) > 512

    matcher = ASTMatcher(str(path))
    result = matcher.evaluate(
        "def fallback_fn():\n\n    return 1",
        "def fallback_fn():\n    return 99",
    )
    assert result.success
    assert result.strategy in ("ast-fuzzy", "whitespace-fuzzy")
    assert "return 99" in result.content
    assert "return 1" not in result.content.split("return 99")[0].split("fallback_fn")[-1]


def test_ast_window_disabled_for_small_files(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_AST_WINDOW_BYTES", "8192")
    monkeypatch.setenv("APATCH_AST_FULL_PARSE_MAX", "999999")

    path = tmp_path / "small.py"
    path.write_text("def small():\n    return 1\n", encoding="utf-8")

    matcher = ASTMatcher(str(path))
    result = matcher.evaluate(
        "def small():\n\n    return 1",
        "def small():\n    return 2",
    )
    assert result.success
    assert "return 2" in result.content


def test_find_parse_anchor_whitespace_drift():
    content = "def foo():\n    return 1\n"
    old = "def foo():\n\n    return 1"
    assert find_parse_anchor(content, old) == 0


# ---------------------------------------------------------------------------
# SCALE-6 — parallel plan
# ---------------------------------------------------------------------------


def test_evaluate_plan_entry_exact(tmp_path):
    src = tmp_path / "main.py"
    src.write_text("x = 1\n", encoding="utf-8")
    payload = {
        "entry": {
            "step_index": 1,
            "resolved_path": str(src),
            "strategy": None,
            "confidence": 0.0,
            "would_apply": False,
            "warnings": [],
            "diff": "",
        },
        "resolved_path": str(src),
        "old_content": "x = 1",
        "new_content": "x = 2",
        "action_type": "REPLACE",
        "replace_all": False,
        "show_diff": False,
        "basename": "main.py",
    }
    out = evaluate_plan_entry(payload)
    assert out["strategy"] == "exact"
    assert out["would_apply"] is True
    assert out["confidence"] == 1.0


def test_parallel_plan_evaluate_preserves_order(tmp_path):
    files = []
    jobs = []
    for i in range(4):
        p = tmp_path / f"f{i}.py"
        p.write_text(f"v = {i}\n", encoding="utf-8")
        files.append(p)
        jobs.append({
            "entry": {"step_index": i, "warnings": [], "diff": ""},
            "resolved_path": str(p),
            "old_content": f"v = {i}",
            "new_content": f"v = {i + 10}",
            "action_type": "REPLACE",
            "replace_all": False,
            "show_diff": False,
            "basename": p.name,
        })

    results = parallel_plan_evaluate(jobs, workers=2)
    assert len(results) == 4
    assert [r["step_index"] for r in results] == [0, 1, 2, 3]
    assert all(r["would_apply"] for r in results)


def test_parallel_plan_evaluate_falls_back_without_process_pool(tmp_path, monkeypatch):
    import concurrent.futures

    class DeniedProcessPool:
        def __init__(self, *args, **kwargs):
            raise PermissionError("process semaphores are unavailable")

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", DeniedProcessPool)
    src = tmp_path / "fallback.py"
    src.write_text("value = 1\n", encoding="utf-8")
    jobs = [
        {
            "entry": {"step_index": i, "warnings": [], "diff": ""},
            "resolved_path": str(src),
            "old_content": "value = 1",
            "new_content": f"value = {i + 2}",
            "action_type": "REPLACE",
            "replace_all": False,
            "show_diff": False,
            "basename": src.name,
        }
        for i in range(2)
    ]

    results = parallel_plan_evaluate(jobs, workers=2)

    assert [row["step_index"] for row in results] == [0, 1]
    assert all(row["would_apply"] for row in results)


def test_plan_cli_parallel_json(tmp_path):
    from click.testing import CliRunner

    from apatch.cli import cli

    for i in range(3):
        (tmp_path / f"file{i}.py").write_text(f"a = {i}\n", encoding="utf-8")

    log = tmp_path / "session.jsonl"
    steps = []
    for i in range(3):
        steps.append(json.dumps({
            "step_index": i + 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": f"file{i}.py",
                    "TargetContent": f"a = {i}",
                    "ReplacementContent": f"a = {i + 100}",
                },
            }],
        }))
    log.write_text("\n".join(steps) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["plan", "--logs", str(log), "--target-dir", str(tmp_path), "--json", "--workers", "2"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 3
    assert all(row["would_apply"] for row in data)
    assert all(row["strategy"] == "exact" for row in data)


def test_get_plan_workers_env_and_flag(monkeypatch):
    monkeypatch.setenv("APATCH_PLAN_WORKERS", "3")
    assert get_plan_workers(None) == 3
    assert get_plan_workers(0) == 0
    assert get_plan_workers(-1) == -1


# ---------------------------------------------------------------------------
# SCALE-7 — TrustChain signature index
# ---------------------------------------------------------------------------


def test_signature_index_cached_lookup(tmp_path, monkeypatch):
    tc = tmp_path / ".trustchain"
    obj_dir = tc / "objects" / "ab"
    obj_dir.mkdir(parents=True)
    payload = {"signature": "deadbeef", "id": "operation-42"}
    (obj_dir / "rec.json").write_text(json.dumps(payload), encoding="utf-8")

    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    helper.trustchain_dir = str(tc)

    walk_calls = {"n": 0}
    real_walk = os.walk

    def counting_walk(top, *args, **kwargs):
        walk_calls["n"] += 1
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", counting_walk)

    assert helper._find_op_id_by_signature("deadbeef") == "operation-42"
    assert helper._find_op_id_by_signature("deadbeef") == "operation-42"
    assert walk_calls["n"] == 1


def test_signature_index_invalidated_after_commit(tmp_path, monkeypatch):
    tc = tmp_path / ".trustchain"
    (tc / "objects").mkdir(parents=True)
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    helper.trustchain_dir = str(tc)
    helper._signature_index = {"cached": "op-old"}

    monkeypatch.setattr(helper, "_commit_via_subprocess", lambda *_a, **_k: True)
    monkeypatch.setattr(helper, "_maybe_push_to_platform", lambda *_a, **_k: None)
    monkeypatch.setattr(helper, "has_trustchain", lambda: True)
    monkeypatch.setattr(helper, "_policy_allows", lambda *_a, **_k: True)

    helper.commit_action("apatch", {"action": "test"})
    assert helper._signature_index is None


# ---------------------------------------------------------------------------
# SCALE-8 — optional path backends
# ---------------------------------------------------------------------------


def test_path_index_git_forced_backend(tmp_path, monkeypatch):
    if not __import__("shutil").which("git"):
        pytest.skip("git not installed")
    monkeypatch.setenv("APATCH_PATH_BACKEND", "git")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    tracked = repo / "lib.py"
    tracked.write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "lib.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    index = PathIndex.build(str(repo), backend="git")
    assert index.source == "git"
    assert os.path.abspath(tracked) in index.resolve_basename("lib.py")


def test_path_index_fd_backend_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_PATH_BACKEND", "fd")
    target = tmp_path / "proj"
    (target / "src").mkdir(parents=True)
    tracked = target / "src" / "from_fd.py"
    tracked.write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/mock/fd" if cmd == "fd" else None,
    )

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "/mock/fd"
        return SimpleNamespace(returncode=0, stdout=str(tracked).encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    index = PathIndex.build(str(target))
    assert index.source == "fd"
    assert os.path.abspath(tracked) in index.resolve_basename("from_fd.py")


def test_path_index_fd_fallback_to_walk(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_PATH_BACKEND", "fd")
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    target = tmp_path / "proj"
    (target / "src").mkdir(parents=True)
    f = target / "src" / "walk.py"
    f.write_text("y", encoding="utf-8")

    index = PathIndex.build(str(target))
    assert index.source == "walk"
    assert os.path.abspath(f) in index.resolve_basename("walk.py")


def test_path_index_watchman_backend_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_PATH_BACKEND", "watchman")
    target = tmp_path / "proj"
    (target / "src").mkdir(parents=True)
    wm_file = target / "src" / "indexed.py"
    wm_file.write_text("z", encoding="utf-8")

    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/mock/watchman" if cmd == "watchman" else None,
    )

    query_response = json.dumps({"files": {"src/indexed.py": {"exists": True}}})

    def fake_run(cmd, **kwargs):
        if cmd[1] == "watch-project":
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if cmd[1] == "-j":
            return SimpleNamespace(returncode=0, stdout=query_response.encode(), stderr=b"")
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    index = PathIndex.build(str(target))
    assert index.source == "watchman"
    assert os.path.abspath(wm_file) in index.resolve_basename("indexed.py")


# ---------------------------------------------------------------------------
# SCALE-1 / SCALE-4 — session integration
# ---------------------------------------------------------------------------


def test_tui_path_index_used_for_multiple_resolves(tmp_path, monkeypatch):
    from apatch.tui import InteractiveTUI

    target = tmp_path / "project"
    (target / "deep").mkdir(parents=True)
    found = target / "deep" / "target.py"
    found.write_text("a = 1\n", encoding="utf-8")

    walk_calls = {"n": 0}
    real_walk = os.walk

    def counting_walk(top, *args, **kwargs):
        walk_calls["n"] += 1
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", counting_walk)

    tui = InteractiveTUI([], str(target), no_trustchain=True)
    walks_after_index_build = walk_calls["n"]
    assert tui.path_index.source in ("walk", "git")

    r1 = tui._resolve_smart_path("/elsewhere/target.py")
    r2 = tui._resolve_smart_path("/tmp/target.py")
    assert r1 == r2 == os.path.abspath(found)
    assert walk_calls["n"] == walks_after_index_build


def test_match_session_invalidate_after_write(tmp_path):
    path = tmp_path / "mod.py"
    path.write_text("v = 1\n", encoding="utf-8")

    session = MatchSession()
    m1 = session.matcher_for(str(path))
    assert m1.content == "v = 1\n"

    path.write_text("v = 2\n", encoding="utf-8")
    m_stale = session.matcher_for(str(path))
    assert m_stale is m1
    assert m_stale.content == "v = 1\n"

    session.invalidate(str(path))
    m_fresh = session.matcher_for(str(path))
    assert m_fresh is not m1
    assert m_fresh.content == "v = 2\n"


def test_get_ast_window_config_from_env(monkeypatch):
    monkeypatch.setenv("APATCH_AST_WINDOW_BYTES", "12345")
    monkeypatch.setenv("APATCH_AST_FULL_PARSE_MAX", "999")
    window, full_max = get_ast_window_config()
    assert window == 12345
    assert full_max == 999


def test_resolve_path_backend_explicit():
    assert resolve_path_backend("fd") == "fd"
    assert resolve_path_backend("watchman") == "watchman"
