import os
import json
import subprocess
import pytest
from apatch.trustchain_helper import TrustChainHelper

def test_real_trustchain_integration_loop(tmp_path, require_tc):
    # This integration test verifies apatch's TrustChainHelper against the REAL
    # globally installed 'tc' CLI and TrustChain library on the system.
    
    # 1. Initialize a real .trustchain repository in a temporary directory
    cmd_init = ["tc", "init", "-o", str(tmp_path)]
    res_init = subprocess.run(cmd_init, capture_output=True, text=True, cwd=tmp_path)
    assert res_init.returncode == 0, f"tc init failed: {res_init.stderr}"
    
    helper = TrustChainHelper(str(tmp_path))
    assert helper.has_trustchain()
    assert os.path.isdir(helper.trustchain_dir)
    
    # 2. Commit a real operation to make HEAD non-empty (required for checkpoints)
    payload = {"action": "initialize_test", "status": "active"}
    success_commit = helper.commit_action(tool_id="test_runner", payload=payload)
    assert success_commit, "Failed to sign and commit live operation to TrustChain"
    
    # Verify a commit object actually exists in the local objects store
    objects_dir = os.path.join(helper.trustchain_dir, "objects")
    object_files = os.listdir(objects_dir)
    assert len(object_files) > 0, "No signed objects found in .trustchain/objects/"
    
    # 3. Create a real checkpoint using the live CLI
    checkpoint_name = "apatch_sess_integration_test"
    res_ckpt = helper.create_checkpoint(checkpoint_name)
    assert res_ckpt == checkpoint_name, "Failed to create live TrustChain checkpoint"
    
    # Verify the checkpoint ref file exists and is not empty
    ref_path = os.path.join(helper.trustchain_dir, "refs", "checkpoints", f"{checkpoint_name}.ref")
    assert os.path.exists(ref_path)
    with open(ref_path, "r", encoding="utf-8") as f:
        ckpt_sig = f.read().strip()
    assert len(ckpt_sig) > 0
    
    # 4. Commit a second operation to advance HEAD
    payload_2 = {"action": "modify_test", "status": "drifted"}
    success_commit_2 = helper.commit_action(tool_id="test_runner", payload=payload_2)
    assert success_commit_2
    
    # Assert HEAD has changed
    with open(os.path.join(helper.trustchain_dir, "HEAD"), "r", encoding="utf-8") as f:
        head_sig_advanced = f.read().strip()
    assert head_sig_advanced != ckpt_sig
    
    # 5. Execute a real soft-reset rollback to the checkpoint
    try:
        success_rollback = helper.rollback_checkpoint(checkpoint_name)
        assert success_rollback, "Failed to execute live soft-reset rollback"
    except AssertionError as e:
        # Print diagnostics for debugging
        print("\n=== DIAGNOSTICS ===")
        print(f"checkpoint_name: {checkpoint_name}")
        print(f"ckpt_sig: {ckpt_sig}")
        print(f"head_sig_advanced: {head_sig_advanced}")
        
        objects_dir = os.path.join(helper.trustchain_dir, "objects")
        print(f"objects_dir exists: {os.path.isdir(objects_dir)}")
        if os.path.isdir(objects_dir):
            for root, dirs, files in os.walk(objects_dir):
                for filename in files:
                    if filename.endswith(".json"):
                        p = os.path.join(root, filename)
                        try:
                            with open(p, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                print(f"File: {os.path.relpath(p, objects_dir)}")
                                print(f"  id: {data.get('id')}")
                                print(f"  signature: {data.get('signature')}")
                        except Exception as ex:
                            print(f"  error reading {filename}: {ex}")
        raise e
    
    # Verify HEAD signature has been restored cryptographically to the checkpoint signature
    with open(os.path.join(helper.trustchain_dir, "HEAD"), "r", encoding="utf-8") as f:
        head_sig_restored = f.read().strip()
    assert head_sig_restored == ckpt_sig

def test_trustchain_commit_enrichment(tmp_path, require_tc):
    from apatch.ingestor import PatchCandidate
    from apatch.tui import InteractiveTUI
    import hashlib
    
    # 1. Initialize a real .trustchain repository in a temporary directory
    cmd_init = ["tc", "init", "-o", str(tmp_path)]
    subprocess.run(cmd_init, check=True, capture_output=True)
    
    # 2. Establish HEAD by committing a manual sign
    helper = TrustChainHelper(str(tmp_path))
    success = helper.commit_action("test_runner", {"action": "init"})
    assert success
    
    # 3. Create a target file to patch
    target_file = tmp_path / "app.py"
    target_file.write_text("def run():\n    pass", encoding="utf-8")
    
    candidate = PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file="app.py",
        old_content="def run():\n    pass",
        new_content="def run():\n    print('applied!')"
    )
    
    # 4. Apply the patch under active TrustChain checking
    tui = InteractiveTUI([candidate], str(tmp_path), non_interactive=True)
    tui.run_apply_loop()
    
    # 5. Verify the files dictionary was committed to TrustChain objects
    objects_dir = os.path.join(helper.trustchain_dir, "objects")
    object_files = os.listdir(objects_dir)
    assert len(object_files) > 0
    
    found_files_payload = False
    for filename in object_files:
        if filename.endswith(".json"):
            p = os.path.join(objects_dir, filename)
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                val = data.get("value") or data
                payload_data = val.get("data") if isinstance(val, dict) and "data" in val else val
                if isinstance(payload_data, dict) and "files" in payload_data:
                    found_files_payload = True
                    files_meta = payload_data["files"]
                    assert "app.py" in files_meta
                    meta = files_meta["app.py"]
                    
                    # Assert correct SHA-256 hash
                    hasher = hashlib.sha256()
                    hasher.update(b"def run():\n    print('applied!')")
                    assert meta["sha256"] == hasher.hexdigest()
                    
                    # Assert diff exists and matches expected lines
                    diff = meta["diff"]
                    assert "+    print('applied!')" in diff
                    assert "-    pass" in diff
                    
    assert found_files_payload, "Enriched files payload was not found in TrustChain objects ledger"
