"""MCP default workspace resolution (CURSOR_PROJECT_DIR > stale APATCH_WORKSPACE)."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from apatch.mcp.bound_workspace import (
    bind_mcp_workspace,
    discover_bound_workspace,
    resolve_target_dir,
)
from apatch.mcp.workspace_registry import WorkspaceRegistryError


def test_cursor_project_dir_wins_over_apatch_workspace(tmp_path, monkeypatch):
    apatch = tmp_path / "apatch-proj"
    other = tmp_path / "other-proj"
    for p in (apatch, other):
        (p / ".apatch").mkdir(parents=True)
        (p / ".apatch" / "mcp.json").write_text('{"mcpServers":{"apatch":{}}}\n', encoding="utf-8")
    monkeypatch.setenv("APATCH_WORKSPACE", str(other))
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(apatch))
    assert discover_bound_workspace() == str(apatch.resolve())


def test_resolve_target_dir_dot_uses_bound():
    with mock.patch("apatch.mcp.bound_workspace.bound_mcp_workspace", return_value="/repo/apatch"):
        assert resolve_target_dir(".") == "/repo/apatch"
        assert resolve_target_dir("/abs/path") == "/abs/path"


def test_bind_mcp_workspace_pins_default():
    bind_mcp_workspace("/tmp/ws")
    assert resolve_target_dir(".") == "/tmp/ws"


def test_resolve_registered_alias(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    workspace = tmp_path / "crm"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".apatch").mkdir()
    (workspace / ".apatch" / "mcp.json").write_text(
        '{"mcpServers":{"apatch":{}}}\n',
        encoding="utf-8",
    )
    (workspace / "AGENTS.md").write_text("# contract\n", encoding="utf-8")
    monkeypatch.setenv("APATCH_WORKSPACE_REGISTRY", str(registry))

    from apatch.mcp.workspace_registry import register_local_workspace

    register_local_workspace("crm", str(workspace))
    assert resolve_target_dir("@crm") == str(workspace.resolve())


def test_alias_only_policy_blocks_raw_off_bound_path(tmp_path, monkeypatch):
    bound = tmp_path / "bound"
    other = tmp_path / "other"
    bound.mkdir()
    other.mkdir()
    monkeypatch.setenv("APATCH_MCP_TARGET_POLICY", "alias_only")
    with mock.patch(
        "apatch.mcp.bound_workspace.bound_mcp_workspace",
        return_value=str(bound),
    ):
        assert resolve_target_dir(str(bound)) == str(bound)
        with pytest.raises(WorkspaceRegistryError) as exc:
            resolve_target_dir(str(other))
    assert exc.value.error_type == "WORKSPACE_ALIAS_REQUIRED"
