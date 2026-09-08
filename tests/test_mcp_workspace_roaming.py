"""MCP surface for safe local workspace roaming."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")

from apatch.mcp.server import apatch_workspace_inspect, apatch_workspace_list
from apatch.workflows import workspace_register_workspace


def test_workspace_mcp_list_and_contract_inspect(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_WORKSPACE_REGISTRY", str(tmp_path / "registry.json"))
    root = tmp_path / "crm"
    (root / ".git").mkdir(parents=True)
    (root / ".apatch").mkdir()
    (root / ".apatch" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"apatch": {"env": {}}}}),
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# crm contract\n", encoding="utf-8")

    created = workspace_register_workspace("crm", str(root))
    assert created["ok"] is True
    listing = apatch_workspace_list(target_dir=".")
    assert listing["count"] == 1
    inspected = apatch_workspace_inspect(
        alias="crm",
        include_contract=True,
        target_dir=".",
    )
    assert inspected["target_dir"] == "@crm"
    assert inspected["contract"]["content"] == "# crm contract\n"
