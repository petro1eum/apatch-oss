import os
import json
import pytest
from click.testing import CliRunner
from apatch.cli import cli
from apatch.backup import BackupManager
from apatch.ingestor import PatchCandidate
from apatch.tui import InteractiveTUI

def test_selective_steps_filtering(tmp_path):
    # Create sample transcript logs
    logs_file = tmp_path / "agent.jsonl"
    logs_content = [
        {"step_index": 1, "tool_name": "replace_file_content", "target_file": "main.py", "old_content": "a", "new_content": "b"},
        {"step_index": 2, "tool_name": "replace_file_content", "target_file": "main.py", "old_content": "b", "new_content": "c"},
        {"step_index": 3, "tool_name": "replace_file_content", "target_file": "main.py", "old_content": "c", "new_content": "d"},
        {"step_index": 4, "tool_name": "replace_file_content", "target_file": "main.py", "old_content": "d", "new_content": "e"}
    ]
    with open(logs_file, "w", encoding="utf-8") as f:
        for line in logs_content:
            f.write(json.dumps(line) + "\n")

    runner = CliRunner()
    
    # 1. Test filtering by specific steps
    result = runner.invoke(cli, ["view", "--logs", str(logs_file), "--steps", "1,3"])
    assert result.exit_code == 0
    assert "Total: 2 patch candidates listed." in result.output

    # 2. Test filtering by range
    result_range = runner.invoke(cli, ["view", "--logs", str(logs_file), "--range", "2-4"])
    assert result_range.exit_code == 0
    assert "Total: 3 patch candidates listed." in result_range.output

def test_backup_and_rollback_transactional(tmp_path):
    target_file = tmp_path / "app.py"
    target_file.write_text("initial_state", encoding="utf-8")
    
    # Create backup manager
    mgr = BackupManager(str(tmp_path), session_id="test_sess")
    
    # 1. Assert backup created successfully
    success = mgr.create_backup(str(target_file))
    assert success
    
    # Modify the original file
    target_file.write_text("modified_state", encoding="utf-8")
    
    # 2. Restore file
    restored = mgr.restore_file(str(target_file))
    assert restored
    assert target_file.read_text(encoding="utf-8") == "initial_state"
    
    # Modify again and do session rollback
    target_file.write_text("new_modified_state", encoding="utf-8")
    mgr.create_backup(str(target_file)) # should preserve initial_state because of session check
    target_file.write_text("another_state", encoding="utf-8")
    
    # Session rollback
    restored_list = BackupManager.rollback_session(str(tmp_path), "test_sess")
    assert str(target_file) in restored_list
    assert target_file.read_text(encoding="utf-8") == "initial_state"

def test_apply_dry_run(tmp_path):
    target_file = tmp_path / "main.py"
    target_file.write_text("original_text", encoding="utf-8")
    
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="main.py",
        old_content="original_text",
        new_content="new_text"
    )
    
    # Run in dry-run autopilot mode
    tui = InteractiveTUI([candidate], str(tmp_path), non_interactive=True, dry_run=True)
    tui.run_apply_loop()
    
    # The file should NOT be changed on disk
    assert target_file.read_text(encoding="utf-8") == "original_text"

def test_apply_verify_command_success(tmp_path):
    target_file = tmp_path / "main.py"
    target_file.write_text("original_text", encoding="utf-8")
    
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="main.py",
        old_content="original_text",
        new_content="new_text"
    )
    
    # Verification cmd that succeeds: 'echo "hello"'
    tui = InteractiveTUI([candidate], str(tmp_path), non_interactive=True, verify_cmd="echo 'hello'")
    tui.run_apply_loop()
    
    # File should be modified on disk since verification command passed
    assert target_file.read_text(encoding="utf-8") == "new_text"

def test_apply_verify_command_failure(tmp_path):
    target_file = tmp_path / "main.py"
    target_file.write_text("original_text", encoding="utf-8")
    
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="main.py",
        old_content="original_text",
        new_content="new_text"
    )
    
    # Verification cmd that fails: 'false' or exits with non-zero
    # Using 'exit 1' or 'false'
    tui = InteractiveTUI([candidate], str(tmp_path), non_interactive=True, verify_cmd="exit 1")
    tui.run_apply_loop()
    
    # File should have been rolled back to original_text since verification command failed
    assert target_file.read_text(encoding="utf-8") == "original_text"

def test_apply_verify_deferred_success(tmp_path):
    target_file = tmp_path / "main.py"
    target_file.write_text("original_text", encoding="utf-8")
    
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="main.py",
        old_content="original_text",
        new_content="new_text"
    )
    
    tui = InteractiveTUI([candidate], str(tmp_path), non_interactive=True, verify_cmd="echo 'hello'", verify_deferred=True)
    tui.run_apply_loop()
    
    # File should be modified on disk since verification command passed
    assert target_file.read_text(encoding="utf-8") == "new_text"

def test_apply_verify_deferred_failure(tmp_path):
    target_file = tmp_path / "main.py"
    target_file.write_text("original_text", encoding="utf-8")
    
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="main.py",
        old_content="original_text",
        new_content="new_text"
    )
    
    # Deferred verification cmd that fails
    tui = InteractiveTUI([candidate], str(tmp_path), non_interactive=True, verify_cmd="exit 1", verify_deferred=True)
    tui.run_apply_loop()
    
    # File should have been rolled back to original_text since verification command failed
    assert target_file.read_text(encoding="utf-8") == "original_text"

def test_encoding_auto_detection_and_crlf(tmp_path):
    # Create file in CP1251 encoding with CRLF (\r\n) newlines
    target_file = tmp_path / "legacy.cpp"
    content = "line1\r\nline_to_replace\r\nline3\r\n"
    target_file.write_bytes(content.encode("cp1251"))

    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="legacy.cpp",
        old_content="line_to_replace\n",
        new_content="replaced_crlf_line\n"
    )

    tui = InteractiveTUI([candidate], str(tmp_path), non_interactive=True)
    tui.run_apply_loop()

    # Verify the file was updated, CP1251 encoding was preserved, and CRLF format restored
    assert target_file.exists()
    raw_bytes = target_file.read_bytes()
    decoded = raw_bytes.decode("cp1251")
    assert "replaced_crlf_line" in decoded
    assert "\r\n" in decoded
    assert "\r\nline_to_replace" not in decoded

def test_file_lifecycle_create_delete_rollback(tmp_path):
    # 1. DELETE target file
    old_file = tmp_path / "old_helper.py"
    old_file.write_text("def legacy(): pass", encoding="utf-8")

    # 2. CREATE target file (non-existent)
    new_file = tmp_path / "new_helper.py"

    candidates = [
        PatchCandidate(
            step_index=1,
            tool_name="delete_file",
            target_file="old_helper.py",
            old_content="",
            new_content="",
            action_type="DELETE"
        ),
        PatchCandidate(
            step_index=2,
            tool_name="write_to_file",
            target_file="new_helper.py",
            old_content="",
            new_content="def modern(): pass",
            action_type="CREATE"
        )
    ]

    tui = InteractiveTUI(candidates, str(tmp_path), non_interactive=True)
    tui.run_apply_loop()

    # Check deletions and creations took effect
    assert not old_file.exists()
    assert new_file.exists()
    assert new_file.read_text(encoding="utf-8") == "def modern(): pass"

    # Roll back entire session
    BackupManager.rollback_session(str(tmp_path), tui.backup_mgr.session_id)

    # Check that modern file is physically DELETED, and legacy file is physically RESTORED
    assert not new_file.exists()
    assert old_file.exists()
    assert old_file.read_text(encoding="utf-8") == "def legacy(): pass"

def test_step_level_transaction_atomic_rollback(tmp_path):
    file_a = tmp_path / "a.py"
    file_a.write_text("original A", encoding="utf-8")

    file_b = tmp_path / "b.py"
    file_b.write_text("original B", encoding="utf-8")

    # Both candidates are in step 7. Step 7 should apply atomically.
    candidates = [
        PatchCandidate(
            step_index=7,
            tool_name="replace_file_content",
            target_file="a.py",
            old_content="original A",
            new_content="modified A"
        ),
        PatchCandidate(
            step_index=7,
            tool_name="replace_file_content",
            target_file="b.py",
            old_content="original B",
            new_content="modified B"
        )
    ]

    # Verification fails on step level. Both files must be rolled back!
    tui = InteractiveTUI(candidates, str(tmp_path), non_interactive=True, verify_cmd="exit 1")
    tui.run_apply_loop()

    # Assert step transaction rolled back atomically and left both files intact
    assert file_a.read_text(encoding="utf-8") == "original A"
    assert file_b.read_text(encoding="utf-8") == "original B"
