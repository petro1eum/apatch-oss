import json

from click.testing import CliRunner

from apatch.cli import cli
from apatch.codex_approval import diagnose_codex_prompt_friction, write_codex_approvals


def test_write_codex_approvals_creates_server_and_tool_blocks(tmp_path):
    config = tmp_path / "config.toml"
    result = write_codex_approvals(
        config_path=str(config),
        target_dir=str(tmp_path),
        python="python3",
    )

    text = config.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert result["changed"] is True
    assert result["server_created"] is True
    assert '[mcp_servers.apatch]' in text
    assert 'args = ["-m", "apatch.mcp.workspace_launcher"]' in text
    assert "[mcp_servers.apatch.tools.apatch_remote_task_run]" in text
    assert "[mcp_servers.apatch.tools.apatch_remote_service_action]" in text
    assert 'approval_mode = "approve"' in text


def test_write_codex_approvals_updates_existing_tool_block(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.apatch]
enabled = true

[mcp_servers.apatch.tools.apatch_remote_service_action]
approval_mode = "ask"
""".lstrip(),
        encoding="utf-8",
    )

    result = write_codex_approvals(config_path=str(config), target_dir=str(tmp_path), preset="remote")

    text = config.read_text(encoding="utf-8")
    assert result["changed"] is True
    assert text.count("[mcp_servers.apatch.tools.apatch_remote_service_action]") == 1
    assert 'approval_mode = "approve"' in text
    assert 'approval_mode = "ask"' not in text


def test_write_codex_approvals_is_idempotent(tmp_path):
    config = tmp_path / "config.toml"
    first = write_codex_approvals(config_path=str(config), target_dir=str(tmp_path), preset="remote")
    second = write_codex_approvals(config_path=str(config), target_dir=str(tmp_path), preset="remote")

    assert first["changed"] is True
    assert second["changed"] is False
    assert second["changed_tools"] == []


def test_mcp_codex_approve_cli_json(tmp_path):
    config = tmp_path / "config.toml"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "mcp",
            "codex-approve",
            "--config",
            str(config),
            "--target-dir",
            str(tmp_path),
            "--python",
            "python3",
            "--preset",
            "remote",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["preset"] == "remote"
    assert "apatch_remote_service_action" in payload["tools"]
    assert "apatch_remote_source_handoff" in payload["tools"]
    assert config.exists()



def test_codex_doctor_flags_workspace_mismatch(tmp_path):
    config = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    target = tmp_path / "other"
    workspace.mkdir()
    target.mkdir()
    write_codex_approvals(config_path=str(config), target_dir=str(workspace), python="python3")

    result = diagnose_codex_prompt_friction(config_path=str(config), target_dir=str(target))

    assert result["filesystem_prompts_likely"] is True
    assert result["configured_workspace"] == str(workspace)
    assert any(f["kind"] == "filesystem_scope" for f in result["findings"])
    assert result["autopilot"]["mode"] == "brokered_edits_required"
    assert result["autopilot"]["can_continue_without_filesystem_prompts"] is True
    assert "apply_patch" in " ".join(result["autopilot"]["must_not_use"])
    assert any(item["tool"] == "apatch_apply_session" for item in result["autopilot"]["use_instead"])


def test_mcp_codex_doctor_cli_json(tmp_path):
    config = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    target = tmp_path / "other"
    workspace.mkdir()
    target.mkdir()
    write_codex_approvals(config_path=str(config), target_dir=str(workspace), python="python3")
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "mcp",
            "codex-doctor",
            "--config",
            str(config),
            "--target-dir",
            str(target),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["filesystem_prompts_likely"] is True
    assert any(f["kind"] == "filesystem_scope" for f in payload["findings"])
    assert payload["autopilot"]["mode"] == "brokered_edits_required"
