"""workspace_launcher finds .apatch/mcp.json."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from apatch.mcp.workspace_launcher import (
    canonical_exec_argv,
    find_workspace_root,
    load_canonical_mcp_config,
    main,
)
from apatch.mcp_health import canonical_mcp_config_path, write_project_mcp_config


def test_find_workspace_root_from_env(tmp_path, monkeypatch):
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"apatch": {"command": "python", "args": ["-m", "apatch.mcp.launcher"], "env": {}}}}),
        encoding="utf-8",
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    monkeypatch.setenv("APATCH_WORKSPACE", str(tmp_path))
    assert find_workspace_root() == tmp_path.resolve()


def test_find_workspace_root(tmp_path, monkeypatch):
    """Clear IDE env hints so cwd walk is exercised."""
    for key in (
        "APATCH_WORKSPACE",
        "APATCH_TARGET_DIR",
        "CURSOR_PROJECT_DIR",
        "VSCODE_CWD",
    ):
        monkeypatch.delenv(key, raising=False)
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"apatch": {"command": "python", "args": ["-m", "apatch.mcp.launcher"], "env": {}}}}),
        encoding="utf-8",
    )
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    assert find_workspace_root(sub) == tmp_path.resolve()


def test_write_canonical_mcp_path(tmp_path):
    path = write_project_mcp_config(str(tmp_path))
    assert path == canonical_mcp_config_path(str(tmp_path))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert "apatch" in data["mcpServers"]
    assert data["mcpServers"]["apatch"]["args"] == ["-m", "apatch.mcp.launcher"]


def test_load_canonical_mcp_config(tmp_path):
    write_project_mcp_config(str(tmp_path))
    cfg, p = load_canonical_mcp_config(tmp_path)
    assert cfg["args"] == ["-m", "apatch.mcp.launcher"]
    assert p.endswith(".apatch/mcp.json")


def test_canonical_exec_uses_isolated_python():
    command, argv = canonical_exec_argv(
        {"command": sys.executable, "args": ["-m", "apatch.mcp.launcher"]}
    )
    assert command == sys.executable
    assert argv == [sys.executable, "-I", "-m", "apatch.mcp.launcher"]


def test_workspace_launcher_execs_canonical_runtime(tmp_path, monkeypatch):
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "apatch": {
                        "command": sys.executable,
                        "args": ["-m", "apatch.mcp.launcher"],
                        "env": {"APATCH_MCP_PROFILE": "compact"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APATCH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", "/stale/apatch/source")
    for key in (
        "APATCH_MCP_TARGET_POLICY",
        "APATCH_MCP_BOUND",
        "APATCH_CANONICAL_RUNTIME",
        "APATCH_MCP_BOOTSTRAPPED",
        "APATCH_MCP_PROFILE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(Path.cwd())
    captured = {}

    class ExecCalled(Exception):
        pass

    def fake_execve(command, argv, env):
        captured.update(command=command, argv=argv, env=env)
        raise ExecCalled

    monkeypatch.setattr(os, "execve", fake_execve)
    with pytest.raises(ExecCalled):
        main()
    assert captured["command"] == sys.executable
    assert captured["argv"] == [sys.executable, "-I", "-m", "apatch.mcp.launcher"]
    assert "PYTHONPATH" not in captured["env"]
    assert captured["env"]["APATCH_MCP_BOUND"] == str(tmp_path)
    assert captured["env"]["APATCH_CANONICAL_RUNTIME"] == "1"
    assert captured["env"]["APATCH_MCP_PROFILE"] == "compact"
