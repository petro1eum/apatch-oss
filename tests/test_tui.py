import os
import pytest
from apatch.tui import InteractiveTUI
from apatch.ingestor import PatchCandidate

def test_resolve_smart_path_direct(tmp_path):
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    
    file_a = target_dir / "src" / "main.cpp"
    file_a.parent.mkdir(parents=True)
    file_a.write_text("void hello() {}")
    
    tui = InteractiveTUI([], str(target_dir))
    
    # 1. Test direct absolute path
    resolved = tui._resolve_smart_path(str(file_a))
    assert resolved == os.path.abspath(file_a)
    
    # 2. Test relative path
    resolved_rel = tui._resolve_smart_path("src/main.cpp")
    assert resolved_rel == os.path.abspath(file_a)

def test_resolve_smart_path_prefix_drift(tmp_path):
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    
    file_b = target_dir / "cpp_core" / "src" / "evaluator.cpp"
    file_b.parent.mkdir(parents=True)
    file_b.write_text("int run() { return 0; }")
    
    tui = InteractiveTUI([], str(target_dir))
    
    # Simulate drift path recorded on a different machine
    logged_drift_path = "/Users/otherdeveloper/workspace/project/cpp_core/src/evaluator.cpp"
    
    resolved = tui._resolve_smart_path(logged_drift_path)
    assert resolved == os.path.abspath(file_b)

def test_resolve_smart_path_filename_fallback(tmp_path):
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    
    file_c = target_dir / "nested" / "deeply" / "sparse_omega.cpp"
    file_c.parent.mkdir(parents=True)
    file_c.write_text("class SparseOmega {};")
    
    tui = InteractiveTUI([], str(target_dir))
    
    # Log has only filename or highly mismatched prefix
    logged_mismatched = "/var/folders/temp/sparse_omega.cpp"
    
    resolved = tui._resolve_smart_path(logged_mismatched)
    assert resolved == os.path.abspath(file_c)

def test_tui_non_interactive(tmp_path):
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    
    file_x = target_dir / "src" / "code.py"
    file_x.parent.mkdir(parents=True)
    file_x.write_text("def compute():\n    return 1")
    
    # AI proposed a successful replacement:
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="src/code.py",
        old_content="def compute():\n    return 1",
        new_content="def compute():\n    return 42"
    )
    
    tui = InteractiveTUI([candidate], str(target_dir), non_interactive=True)
    tui.run_apply_loop()
    
    # Check that it auto-applied without prompting!
    assert file_x.read_text() == "def compute():\n    return 42"

def test_tui_rejects_replacement_that_changes_no_bytes(tmp_path):
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    target = target_dir / "same.py"
    target.write_text("value = 1\n", encoding="utf-8")
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="same.py",
        old_content="value = 1",
        new_content="value = 1",
    )

    tui = InteractiveTUI([candidate], str(target_dir), non_interactive=True)
    tui.run_apply_loop()

    assert tui.report_entries[0]["outcome"] == "failed"
    assert tui.report_entries[0]["strategy"] == "no-change"
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_tui_manual_edit(tmp_path):
    from unittest.mock import patch
    
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    
    file_y = target_dir / "src" / "main.py"
    file_y.parent.mkdir(parents=True)
    file_y.write_text("def run():\n    pass")
    
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="src/main.py",
        old_content="def run():\n    pass",
        new_content="def run():\n    print('updated')"
    )
    
    # Mock subprocess.call to simulate the system editor modifying the temp file
    def mock_subprocess_call(args):
        temp_file_path = args[1]
        with open(temp_file_path, "w", encoding="utf-8") as f:
            f.write("def run():\n    print('edited manually!')")
        return 0
        
    tui = InteractiveTUI([candidate], str(target_dir))
    
    # Mock TUI prompts: choose 'e' (edit), then 'y' (confirm apply)
    with patch("subprocess.call", side_effect=mock_subprocess_call) as mock_call, \
         patch.object(tui, "_prompt_user", return_value="e"), \
         patch.object(tui.console, "input", return_value="y"):
         
        tui.run_apply_loop()
        
    assert mock_call.called
    assert file_y.read_text() == "def run():\n    print('edited manually!')"

def test_resolve_smart_path_traversal_protection(tmp_path):
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    
    tui = InteractiveTUI([], str(target_dir))
    
    # 1. Attempt to resolve an absolute path outside target_dir
    resolved = tui._resolve_smart_path("/etc/passwd")
    assert resolved is None
    
    # 2. Attempt to resolve a relative traversal path outside target_dir
    resolved_traversal = tui._resolve_smart_path("../outside_file.py")
    assert resolved_traversal is None

