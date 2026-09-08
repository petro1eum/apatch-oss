"""Toolchain detection speed and profile parity."""

from __future__ import annotations

import subprocess
import time

import pytest

from apatch.path_index import PathIndex, clear_path_index_cache
from apatch.toolchain import clear_toolchain_cache, detect_toolchain


def test_detects_elastic_profile_without_rglob(tmp_path):
    clear_toolchain_cache()
    mapping = tmp_path / "infra" / "index.json"
    mapping.parent.mkdir(parents=True)
    mapping.write_text(
        '{"mappings": {"properties": {"id": {"type": "keyword"}}}}',
        encoding="utf-8",
    )
    data = detect_toolchain(str(tmp_path), use_cache=False)
    assert "elastic" in data["detected_profiles"]


def test_detects_cosmos_profile_without_rglob(tmp_path):
    clear_toolchain_cache()
    bicep = tmp_path / "azure" / "cosmos.bicep"
    bicep.parent.mkdir(parents=True)
    bicep.write_text(
        "resource account 'Microsoft.DocumentDB/databaseAccounts@2021-04-15'",
        encoding="utf-8",
    )
    data = detect_toolchain(str(tmp_path), use_cache=False)
    assert "cosmos" in data["detected_profiles"]


def test_detect_toolchain_uses_cache(tmp_path):
    clear_toolchain_cache()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    first = detect_toolchain(str(tmp_path))
    second = detect_toolchain(str(tmp_path))
    assert first is second


@pytest.mark.slow
def test_detect_toolchain_git_repo_bounded(tmp_path):
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not installed")
    clear_toolchain_cache()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    t0 = time.perf_counter()
    detect_toolchain(str(repo), use_cache=False)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"detect_toolchain too slow on small git repo: {elapsed:.2f}s"


def test_path_index_build_reuses_cache(tmp_path):
    clear_path_index_cache()
    f = tmp_path / "a.py"
    f.write_text("x=1\n", encoding="utf-8")
    first = PathIndex.build(str(tmp_path))
    second = PathIndex.build(str(tmp_path))
    assert first is second
