"""Physical + TrustChain rollback for apatch strip transactions."""
import os
import subprocess

import pytest

from apatch.backup import BackupManager
from apatch.strip import StripSpec, apply_strips
from apatch.trustchain_helper import TrustChainHelper
from apatch.workflows import rollback_workspace


def test_rollback_preview_lists_files_without_writes(tmp_path):
    src = tmp_path / "sample.cpp"
    src.write_text("int before = 1;\n", encoding="utf-8")

    mgr = BackupManager(str(tmp_path), session_id="preview_sess")
    mgr.create_backup(str(src))

    result = rollback_workspace(str(tmp_path), preview=True)
    assert result["ok"] is True
    assert result["preview"] is True
    assert result["count"] == 1
    assert result["files"][0]["path"] == str(src.resolve())
    assert src.read_text(encoding="utf-8") == "int before = 1;\n"


def test_rollback_session_restores_source_file(tmp_path):
    src = tmp_path / "sample.cpp"
    original = "int before = 1;\nint after = 2;\n"
    src.write_text(original, encoding="utf-8")

    helper = TrustChainHelper(str(tmp_path), auto_init=True)
    sess = helper.begin_mutating_session("apatch_strip", source_files=[str(src)])
    assert sess is not None

    specs = [
        StripSpec(
            start="int before",
            until="int after",
            replace="// stripped\n",
        )
    ]
    apply_strips(str(src), specs, dry_run=False)
    assert "stripped" in src.read_text(encoding="utf-8")
    assert "int before = 1" not in src.read_text(encoding="utf-8")

    assert helper.rollback_checkpoint(sess)
    assert src.read_text(encoding="utf-8") == original


def test_rollback_session_with_trustchain_head(tmp_path, require_tc):
    subprocess.run(["tc", "init", "-o", str(tmp_path)], check=True, capture_output=True)

    helper = TrustChainHelper(str(tmp_path))
    helper.commit_action("test", {"action": "s0"})

    sess = helper.begin_mutating_session("apatch_strip")
    assert sess is not None

    with open(os.path.join(helper.trustchain_dir, "refs", "checkpoints", f"{sess}.ref"), "r") as f:
        ckpt_sig = f.read().strip()

    helper.commit_action("test", {"action": "s1"})
    with open(os.path.join(helper.trustchain_dir, "HEAD"), "r") as f:
        assert f.read().strip() != ckpt_sig

    assert helper.rollback_session(sess)
    with open(os.path.join(helper.trustchain_dir, "HEAD"), "r") as f:
        assert f.read().strip() == ckpt_sig
