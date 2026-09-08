import json
import os

from apatch.trustchain_helper import TrustChainHelper


def test_count_signed_blocks_in_chain_dir(tmp_path):
    tc_dir = tmp_path / ".trustchain"
    chain = tc_dir / "chain"
    chain.mkdir(parents=True)
    (tc_dir / "objects").mkdir()
    block = {
        "signature": "ed25519:" + "a" * 64,
        "value": {"tool_id": "apatch", "data": {"files": {"a.py": {"sha256": "x"}}}},
    }
    (chain / "block_0001.json").write_text(json.dumps(block), encoding="utf-8")
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    helper.trustchain_dir = str(tc_dir)
    assert helper.count_signed_blocks() == 1
    rows = helper.iter_ledger_entries()
    assert len(rows) == 1
    assert rows[0].get("tool_id") == "apatch"
