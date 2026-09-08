import json
import sys
import types

from apatch.doctor import build_trustchain_status
from apatch.enforcement import (
    file_sha256,
    load_notarized_index,
    rebuild_notarized_index_from_ledger,
    record_notarized_files,
    record_session_attested,
    verify_notarization_workspace,
    verify_paths_notarized,
    write_enforcement_config,
)
from apatch.ingestor import PatchCandidate
from apatch.trustchain_helper import TrustChainHelper
from apatch.tui import InteractiveTUI


def _fail_scan(*_args, **_kwargs):
    raise AssertionError("historical ledger scan reached the hot path")


def test_commit_receipt_does_not_scan_history(tmp_path, monkeypatch):
    signature = "s" * 88

    class FakeSigner:
        @staticmethod
        def verify(_signed):
            return True

    class FakeChain:
        def __init__(self):
            self.length = 0
            self.record = None

        def head(self):
            return signature if self.length else None

        def show(self, op_id):
            assert op_id == "op_0001"
            return self.record

    class FakeTrustChain:
        def __init__(self, _config):
            self.chain = FakeChain()
            self._signer = FakeSigner()

        def sign(self, *, tool_id, data):
            signed = type("Signed", (), {"signature": signature})()
            self.chain.length = 1
            self.chain.record = {
                "tool": tool_id,
                "data": data,
                "signature": signature,
            }
            (tmp_path / ".trustchain" / "HEAD").write_text(
                signature, encoding="utf-8"
            )
            return signed

    fake_trustchain = types.ModuleType("trustchain")
    fake_trustchain.TrustChain = FakeTrustChain
    fake_trustchain.TrustChainConfig = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "trustchain", fake_trustchain)
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    helper = TrustChainHelper(str(tmp_path), auto_init=True)
    monkeypatch.setattr(helper, "count_signed_blocks", _fail_scan)
    monkeypatch.setattr(helper, "_iter_ledger_json_paths", _fail_scan)
    assert helper.commit_action("test_runner", {"action": "o1-receipt"}) is True
    receipt = helper.last_commit_evidence
    assert receipt["persisted"] is True
    assert receipt["validation"] == "single_object"
    assert receipt["signature_valid"] is True
    assert receipt["record_valid"] is True
    assert receipt["head_valid"] is True
    assert receipt["length_after"] == receipt["length_before"] + 1


def test_apply_batches_notarization_once(tmp_path, monkeypatch):
    TrustChainHelper(str(tmp_path), auto_init=True)
    monkeypatch.setattr(
        TrustChainHelper,
        "begin_mutating_session",
        lambda self, *_args, **_kwargs: "test-checkpoint",
    )
    candidates = []
    for idx in range(1, 4):
        path = tmp_path / f"f{idx}.py"
        path.write_text(f"value = {idx}\n", encoding="utf-8")
        candidates.append(
            PatchCandidate(
                step_index=idx,
                tool_name="replace_file_content",
                target_file=path.name,
                old_content=f"value = {idx}",
                new_content=f"value = {idx + 10}",
            )
        )
    tui = InteractiveTUI(candidates, str(tmp_path), non_interactive=True, quiet=True)
    commits = []
    monkeypatch.setattr(
        tui.tc_helper,
        "commit_action",
        lambda tool_id, payload: commits.append((tool_id, payload)) or True,
    )
    tui.run_apply_loop()
    assert len(commits) == 1
    assert commits[0][0] == "apatch"
    assert commits[0][1]["action"] == "chunk"
    assert set(commits[0][1]["files"]) == {"f1.py", "f2.py", "f3.py"}


def test_verify_notarization_hot_path_does_not_scan_history(tmp_path, monkeypatch):
    write_enforcement_config(str(tmp_path))
    TrustChainHelper(str(tmp_path), auto_init=True)
    source = tmp_path / "svc.py"
    source.write_text("value = 1\n", encoding="utf-8")
    record_notarized_files(
        str(tmp_path),
        {"svc.py": {"sha256": file_sha256(str(source))}},
    )
    monkeypatch.setattr(TrustChainHelper, "count_signed_blocks", _fail_scan)
    monkeypatch.setattr(TrustChainHelper, "iter_ledger_entries", _fail_scan)
    monkeypatch.setattr("apatch.enforcement._git_staged_files", lambda _root: ["svc.py"])
    result = verify_notarization_workspace(str(tmp_path), staged=True)
    assert result["ok"] is True
    assert result["ledger_scan"] == "cached"
    assert result["checked"] == 1


def test_attestation_promotes_only_exact_session_hashes(tmp_path):
    source = tmp_path / "svc.py"
    source.write_text("value = 1\n", encoding="utf-8")
    digest = file_sha256(str(source))
    record_notarized_files(
        str(tmp_path),
        {"svc.py": {"sha256": digest}},
        signature="mutation-signature",
        governed_session_id="session-exact",
    )

    pending = load_notarized_index(str(tmp_path))
    assert pending["version"] == 3
    assert pending["files"]["svc.py"]["attested"] is False
    assert pending["files"]["svc.py"]["governed_session_id"] == "session-exact"
    assert pending["committed_files"] == {}
    assert record_session_attested(str(tmp_path), "session-foreign") == 0

    assert record_session_attested(
        str(tmp_path),
        "session-exact",
        signature="attestation-signature",
        object_path=".trustchain/objects/attest.json",
    ) == 1
    committed = load_notarized_index(str(tmp_path))
    entry = committed["files"]["svc.py"]
    assert entry["sha256"] == digest
    assert entry["attested"] is True
    assert entry["attestation_signature"] == "attestation-signature"
    assert committed["committed_files"]["svc.py"] == entry


def test_working_tree_notarization_ignores_machine_local_cursor_mcp(tmp_path, monkeypatch):
    write_enforcement_config(str(tmp_path))
    TrustChainHelper(str(tmp_path), auto_init=True)
    source = tmp_path / "svc.py"
    source.write_text("value = 1\n", encoding="utf-8")
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
    record_notarized_files(
        str(tmp_path),
        {"svc.py": {"sha256": file_sha256(str(source))}},
    )
    monkeypatch.setattr(
        "apatch.enforcement._git_unstaged_modified",
        lambda _root: ["svc.py", ".cursor/mcp.json"],
    )

    result = verify_notarization_workspace(str(tmp_path), working_tree=True)

    assert result["ok"] is True
    assert result["checked"] == 1
    assert result["ignored"] == 1
    assert result["ignored_paths"] == [".cursor/mcp.json"]
    assert result["violations"] == []


def test_explicit_rebuild_still_scans_and_detects_drift(tmp_path, monkeypatch):
    write_enforcement_config(str(tmp_path))
    helper = TrustChainHelper(str(tmp_path), auto_init=True)
    source = tmp_path / "svc.py"
    source.write_text("actual\n", encoding="utf-8")
    objects = tmp_path / ".trustchain" / "objects"
    (objects / "op_0001.json").write_text(
        json.dumps(
            {
                "value": {
                    "tool": "apatch",
                    "data": {"files": {"svc.py": {"sha256": "0" * 64}}},
                    "signature": "s" * 64,
                }
            }
        ),
        encoding="utf-8",
    )
    calls = 0
    original = TrustChainHelper.iter_ledger_entries

    def counted(self):
        nonlocal calls
        calls += 1
        return original(self)
    monkeypatch.setattr(TrustChainHelper, "iter_ledger_entries", counted)
    result = verify_paths_notarized(str(tmp_path), ["svc.py"], rebuild_index=True)
    assert calls == 1
    assert result["ledger_scan"] == "full_rebuild"
    assert result["ok"] is False
    assert result["violations"][0]["reason"] == "sha256_mismatch"
    assert helper.has_trustchain() is True


def test_rebuild_notarized_index_uses_chronology_not_lexicographic_op_id(tmp_path):
    helper = TrustChainHelper(str(tmp_path), auto_init=True)
    objects = tmp_path / ".trustchain" / "objects"
    old_hash = "1" * 64
    new_hash = "2" * 64

    def write_object(name, created_at, sha256):
        (objects / name).write_text(
            json.dumps(
                {
                    "created_at": created_at,
                    "value": {
                        "id": name.removesuffix(".json"),
                        "tool": "apatch",
                        "data": {"files": {"svc.py": {"sha256": sha256}}},
                        "signature": "s" * 64,
                    },
                }
            ),
            encoding="utf-8",
        )

    # Lexicographic order is op_10000 then op_9999; chronology is the reverse.
    write_object("op_9999.json", 1.0, old_hash)
    write_object("op_10000.json", 2.0, new_hash)

    index = rebuild_notarized_index_from_ledger(str(tmp_path))
    assert index["files"]["svc.py"]["sha256"] == new_hash
    assert helper.has_trustchain() is True


def test_rebuild_separates_latest_mutation_from_latest_attested_state(tmp_path):
    """A later pending mutation must not erase an earlier committed proof."""
    TrustChainHelper(str(tmp_path), auto_init=True)
    objects = tmp_path / ".trustchain" / "objects"
    committed_hash = "1" * 64
    pending_hash = "2" * 64

    def write_object(name, created_at, tool, data):
        (objects / name).write_text(
            json.dumps(
                {
                    "created_at": created_at,
                    "value": {
                        "id": name.removesuffix(".json"),
                        "tool": tool,
                        "data": data,
                        "signature": "s" * 64,
                    },
                }
            ),
            encoding="utf-8",
        )

    write_object(
        "op_1.json",
        1.0,
        "apatch",
        {
            "action": "chunk",
            "governed_session_id": "session-committed",
            "files": {"svc.py": {"sha256": committed_hash}},
        },
    )
    write_object(
        "op_2.json",
        2.0,
        "apatch_attest",
        {"session_id": "session-committed"},
    )
    write_object(
        "op_3.json",
        3.0,
        "apatch",
        {
            "action": "chunk",
            "governed_session_id": "session-pending",
            "files": {"svc.py": {"sha256": pending_hash}},
        },
    )

    index = rebuild_notarized_index_from_ledger(str(tmp_path))

    assert index["files"]["svc.py"]["sha256"] == pending_hash
    assert index["files"]["svc.py"]["attested"] is False
    assert index["committed_files"]["svc.py"]["sha256"] == committed_hash
    assert index["committed_files"]["svc.py"]["attested"] is True


def test_doctor_reports_chunk_notarization(tmp_path):
    write_enforcement_config(str(tmp_path))
    TrustChainHelper(str(tmp_path), auto_init=True)
    status = build_trustchain_status(str(tmp_path), tc_active=True, enforcement_on=True)
    assert status["behaviors"]["notarization_granularity"] == "apply_chunk"
    assert status["behaviors"]["full_ledger_scan"] == "explicit_audit_or_recovery"
    assert "per-apply-chunk" in status["summary"]
