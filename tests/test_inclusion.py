"""External inclusion proofs — tamper-proof ledger off-machine (RFP-005 §5.10)."""

import json

import pytest

pytest.importorskip("trustchain")
from trustchain.v2.merkle import MerkleTree

from apatch.inclusion import (
    load_inclusion_records,
    record_inclusion,
    verify_audit_path,
    verify_inclusion,
)


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Routes proof fetches by op_id; merkle-root by URL substring."""

    def __init__(self, by_op, *, merkle_head=None):
        self._by_op = by_op
        self._merkle_head = merkle_head
        self.calls = []

    def get(self, url):
        if "merkle-root" in url:
            if self._merkle_head is None:
                return _Resp(500)
            return _Resp(200, self._merkle_head)
        op_id = url.rstrip("/").rsplit("/", 1)[-1]
        self.calls.append(op_id)
        return self._by_op.get(op_id, _Resp(404))


def _proof_payload(chunks, index, op_id, *, tamper_root=False):
    tree = MerkleTree.from_chunks(chunks)
    mp = tree.get_proof(index).to_dict()
    root = "0" * 64 if tamper_root else tree.root
    return {
        "op_id": op_id,
        "leaf_index": index,
        "proof": {**mp, "root": root},
        "chain_length": len(chunks),
        "root": root,
    }


def test_record_and_load_dedup(tmp_path):
    record_inclusion(str(tmp_path), op_id="op-1", tool="apatch.plan")
    record_inclusion(str(tmp_path), op_id="op-1", tool="apatch.plan")  # dup
    record_inclusion(str(tmp_path), op_id="op-2", tool="apatch.apply")
    recs = load_inclusion_records(str(tmp_path))
    assert [r["op_id"] for r in recs] == ["op-1", "op-2"]


def test_record_inclusion_caps_growth(tmp_path, monkeypatch):
    from apatch import inclusion

    monkeypatch.setattr(inclusion, "INCLUSION_CAP", 3)
    for i in range(6):
        record_inclusion(str(tmp_path), op_id=f"op-{i}")
    recs = load_inclusion_records(str(tmp_path))
    assert [r["op_id"] for r in recs] == ["op-3", "op-4", "op-5"]  # last 3 kept


def test_verify_skipped_without_records(tmp_path):
    res = verify_inclusion(str(tmp_path), base_url="https://example.test")
    assert res["ok"] is True
    assert "no inclusion records" in res["skipped"]


def test_verify_skipped_without_platform(tmp_path, monkeypatch):
    monkeypatch.delenv("APATCH_PLATFORM_URL", raising=False)
    record_inclusion(str(tmp_path), op_id="op-1")
    res = verify_inclusion(str(tmp_path), base_url=None)
    assert res["ok"] is True
    assert "no platform" in res["skipped"]
    assert res["recorded"] == 1


def test_verify_ok(tmp_path):
    chunks = ["a", "b", "c"]
    tree = MerkleTree.from_chunks(chunks)
    proof = _proof_payload(chunks, 1, "op-A")
    record_inclusion(str(tmp_path), op_id="op-A", tool="t")
    client = _FakeClient(
        {"op-A": _Resp(200, proof)},
        merkle_head={"merkle_root": tree.root, "length": len(chunks)},
    )
    res = verify_inclusion(str(tmp_path), base_url="https://x.test", http_client=client)
    assert res["ok"] is True
    assert res["verified"] == 1
    assert res["checked"] == 1
    assert res["consistency_ok"] is True


def test_verify_missing_fails(tmp_path):
    record_inclusion(str(tmp_path), op_id="op-missing")
    client = _FakeClient({})  # 404
    res = verify_inclusion(str(tmp_path), base_url="https://x.test", http_client=client)
    assert res["ok"] is False
    assert "op-missing" in res["missing"]


class _RaisingClient:
    """Simulates an unreachable platform: every fetch raises."""

    def get(self, url):
        raise ConnectionError("platform unreachable")


def test_verify_network_error_is_degraded_not_fail(tmp_path):
    # A recorded op + an unreachable platform yields `errors`, but that is NOT
    # tamper: the gate must report `degraded` and stay green — a platform blip
    # must never red a clean CI run. (missing/inconsistent still fail; see above.)
    record_inclusion(str(tmp_path), op_id="op-X")
    res = verify_inclusion(str(tmp_path), base_url="https://x.test",
                           http_client=_RaisingClient())
    assert res["ok"] is True
    assert res["degraded"] is True
    assert res["verified"] == 0
    assert res["missing"] == []
    assert res["errors"]


def test_verify_inconsistent_proof_fails(tmp_path):
    record_inclusion(str(tmp_path), op_id="op-bad")
    client = _FakeClient(
        {"op-bad": _Resp(200, _proof_payload(["a", "b", "c"], 1, "op-bad", tamper_root=True))}
    )
    res = verify_inclusion(str(tmp_path), base_url="https://x.test", http_client=client)
    assert res["ok"] is False
    assert "op-bad" in res["inconsistent"]


def test_verify_wrong_op_id_fails(tmp_path):
    record_inclusion(str(tmp_path), op_id="op-want")
    # Server returns a proof labelled with a different op_id.
    client = _FakeClient(
        {"op-want": _Resp(200, _proof_payload(["a", "b"], 0, "op-other"))}
    )
    res = verify_inclusion(str(tmp_path), base_url="https://x.test", http_client=client)
    assert res["ok"] is False
    assert "op-want" in res["inconsistent"]


def test_verify_audit_path_unit():
    tree = MerkleTree.from_chunks(["x", "y", "z", "w"])
    mp = tree.get_proof(2).to_dict()
    assert verify_audit_path(mp["chunk_hash"], mp["siblings"], tree.root) is True
    assert verify_audit_path(mp["chunk_hash"], mp["siblings"], "f" * 64) is False


def test_inclusion_log_is_control_path():
    from apatch.sandbox import is_control_path

    assert is_control_path(".apatch/inclusion.jsonl")


def test_verify_log_fork_at_head_fails(tmp_path):
    chunks = ["a", "b", "c"]
    tree = MerkleTree.from_chunks(chunks)
    proof = _proof_payload(chunks, 2, "op-fork")
    record_inclusion(str(tmp_path), op_id="op-fork")
    # Current head disagrees with proof snapshot at the same chain length.
    client = _FakeClient(
        {"op-fork": _Resp(200, proof)},
        merkle_head={"merkle_root": "f" * 64, "length": len(chunks)},
    )
    res = verify_inclusion(str(tmp_path), base_url="https://x.test", http_client=client)
    assert res["ok"] is False
    assert res["consistency_ok"] is False
    assert any("current merkle_root" in e for e in res["consistency_errors"])


def test_verify_coverage_gap_fails(tmp_path, monkeypatch):
    """Once anchoring started, every local signed entry must be recorded."""
    monkeypatch.setenv("APATCH_PLATFORM_URL", "https://x.test")
    monkeypatch.setenv("APATCH_AGENT_ID", "agent")
    monkeypatch.setenv("APATCH_AGENT_KEY", "/nonexistent/key.pem")

    tc = tmp_path / ".trustchain" / "objects"
    tc.mkdir(parents=True)
    for i in range(3):
        (tc / f"entry{i}.json").write_text(
            json.dumps({"signature": "a" * 88, "tool_id": "apatch.test"}),
            encoding="utf-8",
        )
    record_inclusion(str(tmp_path), op_id="op-only-one")

    chunks = ["only"]
    tree = MerkleTree.from_chunks(chunks)
    proof = _proof_payload(chunks, 0, "op-only-one")
    client = _FakeClient(
        {"op-only-one": _Resp(200, proof)},
        merkle_head={"merkle_root": tree.root, "length": 1},
    )
    res = verify_inclusion(str(tmp_path), base_url="https://x.test", http_client=client)
    assert res["ok"] is False
    assert res["coverage"]["uncovered"] == 2


def test_inclusion_status_reports_gap(tmp_path, monkeypatch):
    from apatch.inclusion import inclusion_status

    monkeypatch.setenv("APATCH_PLATFORM_URL", "https://x.test")
    tc = tmp_path / ".trustchain" / "objects"
    tc.mkdir(parents=True)
    (tc / "e.json").write_text(json.dumps({"signature": "b" * 88}), encoding="utf-8")
    record_inclusion(str(tmp_path), op_id="op-1")
    st = inclusion_status(str(tmp_path))
    assert st["anchored"] is True
    assert st["recorded"] == 1
    assert st["local_signed"] == 1
    assert st["uncovered"] == 0


def test_ci_gate_fails_on_inclusion(tmp_path, monkeypatch):
    """ci-gate wires verify_inclusion under enforcement (RFP-005 §5.10)."""
    import subprocess

    import apatch.inclusion as inc
    from apatch.enforcement import write_enforcement_config
    from apatch.sandbox import write_sandbox_config
    from apatch.sandbox_watch import run_sandbox_ci_gate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    write_sandbox_config(str(tmp_path))
    write_enforcement_config(str(tmp_path))

    monkeypatch.setattr(
        inc,
        "verify_inclusion",
        lambda root: {"ok": False, "missing": ["op-x"], "checked": 1},
    )
    result = run_sandbox_ci_gate(str(tmp_path))
    assert result["ok"] is False
    assert result["reason"] == "external inclusion failed"
