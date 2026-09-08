"""Canonical .apatch/mcp.json and workspace_launcher."""

from __future__ import annotations

import json

from apatch.mcp_health import (
    canonical_mcp_config_path,
    default_ide_stub_paths,
    discover_mcp_configs,
    recommended_ide_mcp_server_block,
    sync_mcp_configs,
    write_ide_mcp_stub,
)


def test_canonical_mcp_config_path(tmp_path):
    assert canonical_mcp_config_path(str(tmp_path)).endswith(".apatch/mcp.json")


def test_discover_missing_canonical(tmp_path):
    cfgs = discover_mcp_configs(str(tmp_path))
    assert len(cfgs) == 1
    assert cfgs[0]["scope"] == "canonical"
    assert cfgs[0].get("error")


def test_sync_writes_canonical_and_ide_stub(tmp_path):
    ide = tmp_path / ".cursor" / "mcp.json"
    paths = sync_mcp_configs(str(tmp_path), ide_paths=[str(ide)], auto_ide=False)
    assert canonical_mcp_config_path(str(tmp_path)) in paths
    assert str(ide) in paths
    stub = json.loads(ide.read_text(encoding="utf-8"))
    assert stub["mcpServers"]["apatch"]["args"] == ["-m", "apatch.mcp.workspace_launcher"]
    canonical = json.loads((tmp_path / ".apatch" / "mcp.json").read_text(encoding="utf-8"))
    assert canonical["mcpServers"]["apatch"]["args"] == ["-m", "apatch.mcp.launcher"]


def test_recommended_ide_block_uses_workspace_launcher():
    cfg = recommended_ide_mcp_server_block()
    assert cfg["args"] == ["-m", "apatch.mcp.workspace_launcher"]
    assert cfg.get("env") == {}


def test_write_ide_stub_idempotent(tmp_path):
    ide = tmp_path / "mcp.json"
    write_ide_mcp_stub(str(ide))
    write_ide_mcp_stub(str(ide))
    data = json.loads(ide.read_text(encoding="utf-8"))
    assert data["mcpServers"]["apatch"]["args"] == ["-m", "apatch.mcp.workspace_launcher"]


def test_default_ide_stub_paths_project_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".cursor").mkdir()
    paths = default_ide_stub_paths(str(tmp_path))
    assert any(p.endswith(".cursor/mcp.json") for p in paths)


def test_sync_auto_ide_upgrades_launcher_to_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".cursor").mkdir()
    ide = tmp_path / ".cursor" / "mcp.json"
    ide.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "apatch": {
                        "command": "/usr/bin/python3",
                        "args": ["-m", "apatch.mcp.launcher"],
                        "env": {"PYTHONIOENCODING": "utf-8"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    sync_mcp_configs(str(tmp_path), auto_ide=True)
    data = json.loads(ide.read_text(encoding="utf-8"))
    assert data["mcpServers"]["apatch"]["args"] == ["-m", "apatch.mcp.workspace_launcher"]
