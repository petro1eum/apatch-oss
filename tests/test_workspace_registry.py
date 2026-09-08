"""Safe local MCP workspace roaming registry."""

from __future__ import annotations

import json

import pytest

from apatch.mcp.workspace_registry import (
    WorkspaceRegistryError,
    inspect_local_workspace,
    list_local_workspaces,
    local_roaming_status,
    register_local_workspace,
    remove_local_workspace,
    resolve_local_workspace,
)


def _workspace(root, contract="# contract\n"):
    (root / ".git").mkdir(parents=True)
    (root / ".apatch").mkdir()
    (root / ".apatch" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"apatch": {"env": {}}}}),
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(contract, encoding="utf-8")
    return root


def test_register_list_inspect_and_resolve(tmp_path, monkeypatch):
    registry = tmp_path / "config" / "workspaces.json"
    monkeypatch.setenv("APATCH_WORKSPACE_REGISTRY", str(registry))
    crm = _workspace(tmp_path / "crm")

    created = register_local_workspace("crm", str(crm))
    assert created["ok"] is True
    assert created["target_dir"] == "@crm"
    assert created["replaced"] is False
    assert registry.stat().st_mode & 0o777 == 0o600

    listing = list_local_workspaces()
    assert listing["count"] == 1
    assert listing["workspaces"][0]["ready"] is True

    inspected = inspect_local_workspace("crm", include_contract=True)
    assert inspected["contract"]["content"] == "# contract\n"
    assert resolve_local_workspace("@crm") == str(crm.resolve())

    status = local_roaming_status(str(crm), bound_workspace="/control")
    assert status["effective_alias"] == "crm"
    assert status["effective_target_dir"] == "@crm"


def test_contract_drift_blocks_resolution_until_human_reauthorizes(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_WORKSPACE_REGISTRY", str(tmp_path / "registry.json"))
    crm = _workspace(tmp_path / "crm")
    register_local_workspace("crm", str(crm))
    (crm / "AGENTS.md").write_text("# changed contract\n", encoding="utf-8")

    status = inspect_local_workspace("crm")
    assert status["ready"] is False
    assert status["drift"][0]["type"] == "WORKSPACE_CONTRACT_DRIFT"
    with pytest.raises(WorkspaceRegistryError) as exc:
        resolve_local_workspace("crm")
    assert exc.value.error_type == "WORKSPACE_CONTRACT_DRIFT"

    refreshed = register_local_workspace("crm", str(crm), force=True)
    assert refreshed["ready"] is True
    assert refreshed["replaced"] is True
    assert resolve_local_workspace("crm") == str(crm.resolve())


def test_unknown_invalid_and_remove_are_structured(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_WORKSPACE_REGISTRY", str(tmp_path / "registry.json"))
    with pytest.raises(WorkspaceRegistryError) as unknown:
        inspect_local_workspace("crm")
    assert unknown.value.to_result()["error_type"] == "WORKSPACE_ALIAS_UNKNOWN"

    with pytest.raises(WorkspaceRegistryError) as invalid:
        register_local_workspace("../crm", str(tmp_path))
    assert invalid.value.error_type == "WORKSPACE_ALIAS_INVALID"

    crm = _workspace(tmp_path / "crm")
    register_local_workspace("crm", str(crm))
    removed = remove_local_workspace("crm")
    assert removed["removed"] is True
    assert list_local_workspaces()["count"] == 0


def test_registration_requires_git_mcp_and_agents_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_WORKSPACE_REGISTRY", str(tmp_path / "registry.json"))
    root = tmp_path / "plain"
    root.mkdir()
    with pytest.raises(WorkspaceRegistryError) as exc:
        register_local_workspace("plain", str(root))
    assert exc.value.error_type == "WORKSPACE_NOT_READY"
