"""Performance and ADR-001 §7 acceptance tests (slow by default)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from apatch.matcher import ASTMatcher
from apatch.path_index import PathIndex, iter_target_files
from apatch.resolver import resolve_smart_path
from tests.fixtures.monorepo import build_synthetic_monorepo, monorepo_file_target


@pytest.mark.slow
def test_monorepo_indexed_resolve_within_10x_single_walk(tmp_path_factory):
    """ADR-001 §7.1: 100 basename-drift resolves with index ≤ 10× one os.walk."""
    root = tmp_path_factory.mktemp("monorepo50k")
    needle = build_synthetic_monorepo(root)
    target = str(root)
    needle_name = needle.name

    # Count files (spot-check scale).
    indexed = PathIndex.build(target)
    indexed_count = sum(len(v) for v in indexed.basename_map.values())
    assert indexed_count >= monorepo_file_target() - 5

    t0 = time.perf_counter()
    one = resolve_smart_path(target, f"/virtual/{needle_name}", index=None)
    single_walk_s = time.perf_counter() - t0
    assert one == str(needle.resolve())

    t0 = time.perf_counter()
    for i in range(100):
        resolved = resolve_smart_path(
            target, f"/drift/machine/{i}/{needle_name}", index=None
        )
        assert resolved == str(needle.resolve())
    baseline_100_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    path_index = PathIndex.build(target)
    build_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    for i in range(100):
        resolved = resolve_smart_path(
            target, f"/drift/machine/{i}/{needle_name}", index=path_index
        )
        assert resolved == str(needle.resolve())
    resolve_only_s = time.perf_counter() - t1
    indexed_total_s = build_s + resolve_only_s

    floor = max(single_walk_s, 0.05)
    assert indexed_total_s <= 10 * floor, (
        f"indexed {indexed_total_s:.2f}s > 10× single walk {single_walk_s:.2f}s; "
        f"baseline100={baseline_100_s:.2f}s build={build_s:.2f}s resolve={resolve_only_s:.4f}s"
    )
    assert indexed_total_s < baseline_100_s / 5, (
        f"indexed path should beat naive 100 walks: {indexed_total_s:.2f}s vs {baseline_100_s:.2f}s"
    )


@pytest.mark.slow
def test_monorepo_plan_cli_100_candidates(tmp_path_factory):
    """ADR-001 §7.1: end-to-end plan --json on 100 drifted paths stays bounded."""
    root = tmp_path_factory.mktemp("monorepo_plan")
    needle = build_synthetic_monorepo(root)
    rel_needle = needle.relative_to(root)

    lines = []
    for i in range(100):
        step = {
            "step_index": i + 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": f"/other/root/{i}/{rel_needle}",
                    "TargetContent": "value = 1",
                    "ReplacementContent": "value = 2",
                },
            }],
        }
        lines.append(json.dumps(step))
    log = root / "session.jsonl"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    from click.testing import CliRunner

    from apatch.cli import cli

    runner = CliRunner()
    t0 = time.perf_counter()
    result = runner.invoke(
        cli,
        ["plan", "--logs", str(log), "--target-dir", str(root), "--json"],
    )
    elapsed = time.perf_counter() - t0

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 100
    assert sum(1 for row in data if row["would_apply"]) == 100
    assert all(row["strategy"] == "exact" for row in data)

    # One index build + 100 cheap resolves; should finish well under naive 100 walks.
    t0 = time.perf_counter()
    for i in range(100):
        resolve_smart_path(str(root), f"/x/{i}/{needle.name}", index=None)
    hundred_walks_s = time.perf_counter() - t0
    assert elapsed <= max(hundred_walks_s / 5, 5.0), (
        f"plan took {elapsed:.2f}s vs 100 walks {hundred_walks_s:.2f}s"
    )


@pytest.mark.slow
def test_monorepo_parallel_plan_workers(tmp_path_factory):
    root = tmp_path_factory.mktemp("monorepo_parallel")
    for i in range(8):
        p = root / f"mod_{i}.py"
        p.write_text(f"x{i} = {i}\n", encoding="utf-8")

    lines = []
    for i in range(8):
        lines.append(json.dumps({
            "step_index": i + 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": f"mod_{i}.py",
                    "TargetContent": f"x{i} = {i}",
                    "ReplacementContent": f"x{i} = {i + 100}",
                },
            }],
        }))
    log = root / "p.jsonl"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    from click.testing import CliRunner

    from apatch.cli import cli

    runner = CliRunner()
    seq = runner.invoke(
        cli,
        ["plan", "--logs", str(log), "--target-dir", str(root), "--json", "--workers", "0"],
    )
    par = runner.invoke(
        cli,
        ["plan", "--logs", str(log), "--target-dir", str(root), "--json", "--workers", "2"],
    )
    assert seq.exit_code == 0 and par.exit_code == 0
    assert json.loads(seq.output) == json.loads(par.output)


def test_prune_scandir_never_enters_git_objects(tmp_path, monkeypatch):
    """ADR-001 §7.2: pruned walk must not scan ``.git/objects``."""
    root = tmp_path / "proj"
    git_obj = root / ".git" / "objects" / "ab"
    src = root / "src"
    git_obj.mkdir(parents=True)
    src.mkdir()
    (git_obj / "blob").write_bytes(b"\x00" * 128)
    (src / "ok.py").write_text("1", encoding="utf-8")

    entered: list[str] = []
    real_scandir = os.scandir

    def tracking_scandir(path):
        entered.append(os.path.abspath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", tracking_scandir)
    list(iter_target_files(str(root)))

    assert not any(
        p.endswith(os.path.join(".git", "objects")) or "/.git/objects/" in p.replace("\\", "/")
        for p in entered
    )


def test_git_index_excludes_untracked_basename(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not installed")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)

    tracked = repo / "tracked.py"
    tracked.write_text("t = 1\n", encoding="utf-8")
    untracked = repo / "untracked.py"
    untracked.write_text("u = 1\n", encoding="utf-8")

    subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    index = PathIndex.build(str(repo))
    assert index.source == "git"
    assert str(tracked.resolve()) in index.resolve_basename("tracked.py")
    assert index.resolve_basename("untracked.py") == []

    # Untracked CREATE still resolves via direct relative path (no index needed).
    created = resolve_smart_path(str(repo), "untracked.py", action_type="CREATE")
    assert created == str(untracked.resolve())


def test_plan_same_file_reuses_match_session(tmp_path):
    """Dry-run plan loads each file once per session (MatchSession cache)."""
    from click.testing import CliRunner

    from apatch.cli import cli
    from apatch.match_session import MatchSession

    src = tmp_path / "once.py"
    src.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")

    lines = []
    for i, (old, new) in enumerate(
        [("x = 1", "x = 10"), ("y = 2", "y = 20"), ("z = 3", "z = 30")],
        start=1,
    ):
        lines.append(json.dumps({
            "step_index": i,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "once.py",
                    "TargetContent": old,
                    "ReplacementContent": new,
                },
            }],
        }))
    log = tmp_path / "once.jsonl"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    constructed = {"n": 0}
    original_init = ASTMatcher.__init__

    def counting_init(self, path, *args, **kwargs):
        constructed["n"] += 1
        return original_init(self, path, *args, **kwargs)

    runner = CliRunner()
    with patch.object(ASTMatcher, "__init__", counting_init):
        result = runner.invoke(
            cli,
            ["plan", "--logs", str(log), "--target-dir", str(tmp_path), "--json"],
        )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 3
    assert constructed["n"] == 1


def test_apply_yes_batch_same_file_e2e(tmp_path):
    """E2E: ``apply -y`` applies sequential patches to one file."""
    from click.testing import CliRunner

    from apatch.cli import cli

    src = tmp_path / "batch.py"
    src.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")

    steps = []
    for i, (old, new) in enumerate(
        [("a = 1", "a = 10"), ("b = 2", "b = 20"), ("c = 3", "c = 30")],
        start=1,
    ):
        steps.append(json.dumps({
            "step_index": i,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "batch.py",
                    "TargetContent": old,
                    "ReplacementContent": new,
                },
            }],
        }))
    log = tmp_path / "batch.jsonl"
    log.write_text("\n".join(steps) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "apply",
            "--logs", str(log),
            "--target-dir", str(tmp_path),
            "-y",
            "--no-trustchain",
        ],
    )

    assert result.exit_code == 0, result.output
    assert src.read_text(encoding="utf-8") == "a = 10\nb = 20\nc = 30\n"
