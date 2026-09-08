"""Package semver — single source of truth (SPEC-VERSION-1)."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
    import pytest

    tomllib = pytest.importorskip("tomli", reason="tomllib (3.11+) or tomli required")

from apatch import __version__


def test_version_matches_pyproject():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == data["project"]["version"]


def test_source_version_uses_tomli_when_tomllib_missing(monkeypatch):
    import builtins
    import types

    import apatch as apatch_package

    real_import = builtins.__import__
    parsed_by_tomli = []

    def fallback_import(name, *args, **kwargs):
        if name == "tomllib":
            raise ModuleNotFoundError("simulated Python 3.10")
        if name == "tomli":
            parsed_by_tomli.append(True)
            return types.SimpleNamespace(loads=tomllib.loads)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fallback_import)

    root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert apatch_package._package_version() == expected["project"]["version"]
    assert parsed_by_tomli == [True]


def test_declared_version_has_release_notes():
    """The version the package declares is the one the release notes describe.

    R3 once demanded `pyproject.toml` be at 0.3.0 -- a one-off release action frozen as
    a standing requirement. Its check was not portable, so the contract gate never ran
    it and never reported that it had been false since 0.3.1.
    """
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [%s]" % version in changelog, "no release notes for %s" % version

    readme = (root / "docs" / "README.md").read_text(encoding="utf-8")
    assert version in readme, "docs package line does not state %s" % version


def test_release_policy_doc_exists():
    root = Path(__file__).resolve().parents[1]
    doc = root / "docs" / "release-versioning.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "pyproject.toml" in text
    assert "CHANGELOG.md" in text
