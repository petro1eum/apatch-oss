import os
import json
import subprocess
import pytest
from apatch.trustchain_helper import TrustChainHelper

def test_trustchain_detection_negative(tmp_path):
    # No .trustchain directory exists
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    assert not helper.has_trustchain()
    assert helper.trustchain_dir is None

def test_auto_init_creates_trustchain(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    helper = TrustChainHelper(str(tmp_path), auto_init=True)
    assert helper.has_trustchain()
    assert (tmp_path / ".trustchain" / "config.json").is_file()
    assert (tmp_path / ".trustchain" / "HEAD").exists()


def test_trustchain_detection_positive(tmp_path):
    # Create temporary .trustchain directory
    tc_dir = tmp_path / ".trustchain"
    tc_dir.mkdir()
    
    helper = TrustChainHelper(str(tmp_path))
    assert helper.has_trustchain()
    assert helper.trustchain_dir == str(tc_dir)

def test_create_checkpoint(tmp_path, require_tc):
    # Initialize a real local TrustChain repository
    cmd_init = ["tc", "init", "-o", str(tmp_path)]
    subprocess.run(cmd_init, check=True, capture_output=True)
    
    helper = TrustChainHelper(str(tmp_path))
    assert helper.has_trustchain()
    
    # Commit a real action to ensure HEAD is not empty
    success_commit = helper.commit_action("test_runner", {"action": "init"})
    assert success_commit
    
    # Create the checkpoint via helper
    res = helper.create_checkpoint("test_ckpt")
    assert res == "test_ckpt"
    
    # Verify checkpoint ref exists and contains a signature
    ref_path = os.path.join(helper.trustchain_dir, "refs", "checkpoints", "test_ckpt.ref")
    assert os.path.exists(ref_path)
    with open(ref_path, "r", encoding="utf-8") as f:
        sig = f.read().strip()
    assert len(sig) > 0

def test_rollback_checkpoint(tmp_path, require_tc):
    # Initialize a real local TrustChain repository
    cmd_init = ["tc", "init", "-o", str(tmp_path)]
    subprocess.run(cmd_init, check=True, capture_output=True)
    
    helper = TrustChainHelper(str(tmp_path))
    
    # 1. Commit first action (state we want to save)
    success = helper.commit_action("test_runner", {"action": "state_1"})
    assert success
    
    # 2. Take checkpoint
    res_ckpt = helper.create_checkpoint("test_ckpt")
    assert res_ckpt == "test_ckpt"
    
    with open(os.path.join(helper.trustchain_dir, "refs", "checkpoints", "test_ckpt.ref"), "r", encoding="utf-8") as f:
        ckpt_sig = f.read().strip()
        
    # 3. Commit a second action to advance HEAD
    success2 = helper.commit_action("test_runner", {"action": "state_2"})
    assert success2
    
    with open(os.path.join(helper.trustchain_dir, "HEAD"), "r", encoding="utf-8") as f:
        advanced_sig = f.read().strip()
    assert advanced_sig != ckpt_sig
    
    # 4. Perform soft-reset rollback via helper
    res = helper.rollback_checkpoint("test_ckpt")
    assert res
    
    # 5. Verify HEAD is restored cryptographically
    with open(os.path.join(helper.trustchain_dir, "HEAD"), "r", encoding="utf-8") as f:
        restored_sig = f.read().strip()
    assert restored_sig == ckpt_sig

def test_commit_action_real(tmp_path, require_tc):
    # Initialize a real local TrustChain repository
    cmd_init = ["tc", "init", "-o", str(tmp_path)]
    subprocess.run(cmd_init, check=True, capture_output=True)
    
    helper = TrustChainHelper(str(tmp_path))
    
    success = helper.commit_action("apatch", {"applied_patches": 5})
    assert success
    
    # Verify the object exists
    objects_dir = os.path.join(helper.trustchain_dir, "objects")
    object_files = os.listdir(objects_dir)
    assert len(object_files) > 0
