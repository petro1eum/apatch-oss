"""CLI coverage for human-only local workspace alias registration."""

from __future__ import annotations

import json

from click.testing import CliRunner

from apatch.cli import cli


def _workspace(root):
    (root / ".git").mkdir(parents=True)
    (root / ".apatch").mkdir()
    (root / ".apatch" / "mcp.json").write_text(
        '{"mcpServers":{"apatch":{"env":{}}}}\n',
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# contract\n", encoding="utf-8")
    return root


def test_workspace_cli_add_list_inspect_remove(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "APATCH_WORKSPACE_REGISTRY",
        str(tmp_path / "config" / "workspaces.json"),
    )
    root = _workspace(tmp_path / "crm")
    runner = CliRunner()

    added = runner.invoke(cli, ["workspace", "add", "crm", str(root), "--json"])
    assert added.exit_code == 0, added.output
    assert json.loads(added.output)["target_dir"] == "@crm"

    listed = runner.invoke(cli, ["workspace", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["count"] == 1

    inspected = runner.invoke(
        cli,
        ["workspace", "inspect", "crm", "--include-contract", "--json"],
    )
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["contract"]["content"] == "# contract\n"

    removed = runner.invoke(cli, ["workspace", "remove", "crm", "--json"])
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.output)["removed"] is True
