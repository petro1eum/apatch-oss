"""Disposable native contract-intake fixture; never the real owner or HOST authority."""
from __future__ import annotations
import base64
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from trustchain import TrustChain, TrustChainConfig
from trustchain.v2.chain_store import verify_record_signature
from trustchain.v2.verifier import TrustChainVerifier
from apatch.sdd_integrity import canonical_hash, freeze_contract, validate_task_envelope
from apatch.trust_identity import _PemKeyProvider

SPEC = "SPEC-IMPORTED-1"
RFP = "RFP-IMPORTED-1"
OWNER = {"id": "intake-owner-fixture", "kind": "machine", "certified": True}
ACTOR = "agent:intake-preparation-fixture"
PENDING = ".apatch/sdd/contract-intake-pending.json"
TOOL = "apatch_sdd_contract_intake"
PROFILE = ".apatch/sdd_verification_contract.json"


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return path


def api():
    # An absent API is deliberately RED, not a setup error or a skipped test.
    name = "apatch.sdd_contract_intake"
    assert importlib.util.find_spec(name) is not None, (
        "Core has no generic fixed-ID owner preparation API; legacy 51F repair "
        "and already-frozen publication do not admit this independent package"
    )
    module = importlib.import_module(name)
    for method in ("prepare_contract_intake", "approve_contract_intake", "review_contract_intake"):
        assert callable(getattr(module, method, None)), "Missing owner intake behavior: " + method
    return module


def package():
    return [
        {"path": "docs/" + RFP + ".md", "content": "# " + RFP + " — Exact owner preparation\r\n\r\n"
            "## Acceptance\r\n\r\n| ID | Level | Criterion |\r\n|---|---|---|\r\n"
            "| IMPORT-1 | MUST | The public result is exactly 42 |\r\n"},
        {"path": "docs/specs/" + SPEC + ".md", "content": "# " + SPEC + " — Exact owner preparation\n\n"
            "> **RFP:** " + RFP + "\n\n## RFP traceability\n\n| RFP id | SPEC Rk | Disposition |\n"
            "|---|---|---|\n| IMPORT-1 | R1 | covered |\n\n## R1 Exact public result\n\n"
            "(verify: python3 -m pytest tests/test_imported.py -q)\n"},
        {"path": "tests/test_imported.py", "content":
            "def test_imported():\n    from feature import public_result\n    assert public_result() == 42\n"},
        {"path": "tests/fixtures/imported.json", "content": '{"expected":42,"prepared_only":true}\n'},
    ]


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def make_workspace(tmp_path, monkeypatch):
    root = tmp_path / "consumer"
    root.mkdir()
    key = tmp_path / "existing-fixture-key.pem"
    private = Ed25519PrivateKey.generate()  # Explicit fixture setup, never production key generation.
    key.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    key.chmod(0o600)
    for name in ("APATCH_AGENT_SIGN_CMD", "APATCH_AGENT_PUBKEY", "APATCH_AGENT_CERT",
                 "APATCH_PLATFORM_URL", "APATCH_PLATFORM_TOKEN", "APATCH_CANONICAL_RUNTIME",
                 "APATCH_POLICY_DENY", "APATCH_LANE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APATCH_AGENT_ID", OWNER["id"])
    monkeypatch.setenv("APATCH_AGENT_KEY", str(key))
    monkeypatch.setenv("APATCH_KEY_BACKEND", "pem")
    public = private.public_key().public_bytes_raw()
    chain = root / ".trustchain"
    chain.mkdir()
    tc = TrustChain(TrustChainConfig(enable_chain=True, chain_storage="file", chain_dir=str(chain),
        key_provider=_PemKeyProvider(str(key), OWNER["id"]), enable_pki=False))
    tc.sign("fixture_genesis", {"generated_identity_only": True})
    old = {
        "docs/RFP-EXISTING-1.md": b"# RFP-EXISTING-1\nOriginal owner obligations.\n",
        "docs/specs/SPEC-EXISTING-1.md": b"# SPEC-EXISTING-1\n## R1 Frozen judge\n",
        "tests/frozen/test_existing.py": b"def test_existing():\n    assert 6 * 7 == 42\n",
    }
    for relative, data in old.items():
        write(root, relative, data)
    assets = [{"path": p, "sha256": digest(v)} for p, v in old.items()]
    command = ["python3", "-m", "pytest", "tests/frozen/test_existing.py", "-q"]
    contract = freeze_contract({
        "brief_hash": "sha256:" + "1" * 64, "rfp_hash": digest(old["docs/RFP-EXISTING-1.md"]),
        "spec_hash": digest(old["docs/specs/SPEC-EXISTING-1.md"]), "plan_hash": "sha256:" + "4" * 64,
        "baseline_hash": "sha256:" + "5" * 64,
        "coverage": {"complete": True, "missing": [], "ambiguous": [], "waivers": []},
        "authority": {"actor_id": OWNER["id"], "role": "authority"},
        "source_mutation_count": 0, "frozen_at": "2026-10-05T00:00:00Z",
        "obligations": [{
            "acceptance_id": "EXISTING-1", "test_id": "tests/frozen/test_existing.py::test_existing",
            "oracle": "The existing frozen owner judge must stay unchanged",
            "perspectives": ["positive", "negative", "boundary", "regression"],
            "asset_hashes": [a["sha256"] for a in assets], "judge_assets": assets,
            "command": command, "command_hash": canonical_hash(command),
            "baseline": {"kind": "observed_red", "result_hash": "sha256:" + "6" * 64},
            "falsification": {"kind": "reversible_seed", "expected": "red",
                "target_hash": "sha256:" + "7" * 64, "target_path": "feature.py"},
            "approver": OWNER["id"], "material": True,
        }],
    })
    envelope = validate_task_envelope({
        "schema": "apatch.sdd.task-envelope.v1", "requirement": "SPEC-EXISTING-1#R1",
        "contract_hash": contract["document_hash"], "baseline_hash": contract["baseline_hash"],
        "allowed_reads": ["docs/**", "tests/**"], "allowed_writes": ["feature.py"],
        "allowed_symbols": ["feature.public_result"],
        "forbidden_paths": ["docs/RFP-*.md", "docs/specs/**", "tests/**"],
        "tools": ["apatch_execute_next"], "commands": [command], "network": [], "remote": [], "services": [],
        "budgets": {"files": 1, "insertions": 10, "deletions": 0, "seconds": 900},
        "checks": ["EXISTING-1"], "rollback_owner": "session:self", "containment": "mediated_only",
    })
    write(root, PROFILE, contract)
    write(root, ".apatch/sdd/frozen/" + contract["document_hash"][7:] + ".json", contract)
    write(root, ".apatch/sdd/envelopes/SPEC-EXISTING-1/R1.json", envelope)
    write(root, ".apatch/locks/SPEC-EXISTING-1/R1.json", {
        "parties": [OWNER], "release": {"state": "locked"}, "fixture_only": True})
    protected = snapshot(root)
    return SimpleNamespace(root=root, key=key, key_bytes=key.read_bytes(), public=public,
                           contract=contract, protected=protected, files=package())


def propose(f, *, files=None, actor=ACTOR):
    return api().prepare_contract_intake(f.root, spec_id=SPEC, rfp_id=RFP, actor_id=actor,
        files=files if files is not None else f.files, reason="Review this exact independent preparation package")


def approve(f, review, *, owner=None, snapshot_hash=None):
    return api().approve_contract_intake(f.root, review, owner=owner if owner is not None else OWNER,
        expected_snapshot=snapshot_hash if snapshot_hash is not None else review["document_hash"])


def native_records(f):
    verifier = TrustChainVerifier(base64.b64encode(f.public).decode(), OWNER["id"], max_age_seconds=None)
    rows = []
    for path in sorted((f.root / ".trustchain/objects").glob("op_*.json")):
        raw = json.loads(path.read_bytes())
        value = raw.get("value", raw)
        assert verify_record_signature(value, verifier) is True
        rows.append(value)
    assert f.key.read_bytes() == f.key_bytes
    return rows


def preserved(f):
    for relative, data in f.protected.items():
        if relative.startswith(".trustchain/") and not relative.startswith(".trustchain/objects/"):
            continue  # Native append must update indexes; historical signed objects remain immutable.
        assert (f.root / relative).read_bytes() == data, "Foreign frozen input changed: " + relative
    assert f.key.read_bytes() == f.key_bytes
    assert not (f.root / ".apatch/sdd/authoring").exists()
    assert not (f.root / ".apatch/locks" / SPEC).exists()
    assert not (f.root / ".apatch/sdd/envelopes" / SPEC).exists()
    assert not (f.root / ".apatch/session_state.json").exists()



def studio_client(f):
    path = Path(__file__).parents[1] / "sdd/test_sdd_owner_execution_flow.py"
    loaded = importlib.util.spec_from_file_location("intake_existing_studio_fixture", path)
    helpers = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(helpers)
    client, headers, runs = helpers._client(f.root)
    identity_path = f.root / ".apatch/machine-identity.json"
    identity = json.loads(identity_path.read_bytes())
    identity.update(machine_name=OWNER["id"], certified=True)
    identity_path.write_text(json.dumps(identity))
    return client, headers, runs
