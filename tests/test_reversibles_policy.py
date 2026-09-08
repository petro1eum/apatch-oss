"""Reversibles bootstrap and policy gating for apatch TrustChain helper."""

import json
import os
import tempfile

from apatch.trustchain_helper import TrustChainHelper


def test_bootstrap_reversibles_json():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "proj")
        os.makedirs(root)
        os.makedirs(os.path.join(root, ".trustchain", "objects"))
        helper = TrustChainHelper(root, auto_init=False)
        helper.trustchain_dir = os.path.join(root, ".trustchain")
        helper._ensure_reversibles()
        rev_path = os.path.join(helper.trustchain_dir, "reversibles.json")
        assert os.path.exists(rev_path)
        data = json.load(open(rev_path, encoding="utf-8"))
        assert data.get("apatch") == "apatch_rollback"


def test_policy_denies_blocked_tool(monkeypatch, require_tc):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "proj")
        os.makedirs(root)
        os.makedirs(os.path.join(root, ".trustchain", "objects"))
        from pathlib import Path

        Path(os.path.join(root, ".trustchain", "HEAD")).write_text("", encoding="utf-8")
        monkeypatch.setenv("APATCH_POLICY_DENY", "danger_tool")
        helper = TrustChainHelper(root, auto_init=False)
        helper.trustchain_dir = os.path.join(root, ".trustchain")
        helper._ensure_policy_hooks()
        assert helper._policy_allows("safe_tool", {"x": 1}) is True
        assert helper._policy_allows("danger_tool", {"x": 1}) is False
