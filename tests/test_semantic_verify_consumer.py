import json
import os

import pytest

from apatch.import_paths import find_semantic_rules_path, resolve_consumer_root
from apatch.semantic_verify import default_rules_path, run_semantic_verify


def test_default_rules_path_uses_consumer_not_git_root(tmp_path):
    repo = tmp_path / "Risk_development_2"
    platform = repo / "platform"
    radar = platform / "src" / "components" / "radar" / "MilitaryRadar.jsx"
    radar.parent.mkdir(parents=True)
    (repo / ".git").mkdir()
    (platform / "src").mkdir(parents=True, exist_ok=True)
    (platform / "package.json").write_text("{}", encoding="utf-8")
    radar.write_text("'use client'\n", encoding="utf-8")

    expected = os.path.join(str(platform), "manifests", "semantic-verify.yaml")
    assert default_rules_path(str(radar)) == expected
    assert resolve_consumer_root(str(radar)) == str(platform)


def test_run_semantic_verify_fallback_toolchain_when_rules_missing(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    src = platform / "src" / "App.tsx"
    src.parent.mkdir(parents=True)
    (platform / "package.json").write_text(
        json.dumps({"scripts": {"build": "echo build-ok"}}),
        encoding="utf-8",
    )
    src.write_text("export {}\n", encoding="utf-8")

    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    result = run_semantic_verify(str(platform), fallback_toolchain=True)
    assert result.get("fallback") == "toolchain_verify"
    assert result.get("rules_missing") is True
    assert "semantic-verify" in (result.get("rules_file") or "")
    assert result.get("ok") is True or result.get("command")


def test_run_semantic_verify_raises_without_fallback(tmp_path):
    platform = tmp_path / "platform"
    (platform / "src").mkdir(parents=True)
    (platform / "package.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="semantic verify rules not found"):
        run_semantic_verify(str(platform), fallback_toolchain=False)


def test_find_semantic_rules_path_returns_none_when_absent(tmp_path):
    platform = tmp_path / "platform"
    (platform / "src").mkdir(parents=True)
    assert find_semantic_rules_path(str(platform)) is None
