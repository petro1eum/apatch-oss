"""Regression tests for MCP/strip reliability fixes."""

import os
from pathlib import Path

import pytest

# apatch.mcp.server imports pydantic, which ships only with the `mcp` extra.
pytest.importorskip("pydantic")

from apatch.imports_resolver import filter_imports_for_content
from apatch.mcp.server import _abs_in_workspace
from apatch.toolchain import build_subprocess_env, discover_npm_path
from apatch.wiring_profiles import build_typescript_wiring_hints
from apatch.strip import StripSpec


def test_abs_in_workspace_resolves_relative_to_target_dir(tmp_path):
    base = tmp_path / "repo"
    base.mkdir()
    rel = _abs_in_workspace(str(base), "src/features/Foo.tsx")
    assert rel == os.path.normpath(str(base / "src/features/Foo.tsx"))


def test_build_subprocess_env_prepends_npm_dir(tmp_path):
    env = build_subprocess_env(str(tmp_path), base_env={"PATH": "/usr/bin"})
    assert "PATH" in env
    assert env["PATH"].startswith("/") or "homebrew" in env["PATH"] or "nvm" in env["PATH"]


def test_discover_npm_path_finds_homebrew_or_nvm():
    npm = discover_npm_path(os.getcwd())
    if npm:
        assert os.path.basename(npm) == "npm"
        assert os.path.isfile(npm)


def test_component_wiring_hints_use_tsx_symbol():
    spec = StripSpec(start="// a", until="// b", module_kind="component")
    hints = build_typescript_wiring_hints(
        spec=spec,
        filename="x.fragment.txt",
        module_out_path="src/features/QuotaBar.tsx",
        label="quota_bar",
        to_module="component",
    )
    assert hints["module_kind"] == "component"
    assert "QuotaBar" in hints["import_line"]
    assert "<QuotaBar" in hints["parent_wire"]
    assert hints["target_module"].endswith(".tsx")


def test_filter_imports_no_bulk_parent_fallback():
    parent = [f"import {{ Thing{i} }} from '@/lib/{i}';" for i in range(20)]
    out = filter_imports_for_content(parent, "unrelatedSymbolOnly", "", "")
    assert len(out) < 12
