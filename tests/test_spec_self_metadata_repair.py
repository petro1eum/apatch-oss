"""R0 self-declaration correction: bounded gate units and real native SDK cycles.

Generated fixture identities are not the user's owner or a production grant.
Unit state construction is explicit; the native-cycle tests separately use
existing disposable Ed25519 material, the real runtime and real verification.
"""
from __future__ import annotations
import base64
import copy
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from trustchain import TrustChain, TrustChainConfig
from trustchain.v2.chain_store import verify_record_signature
from trustchain.v2.verifier import TrustChainVerifier
from apatch.session_state import save_session_state
from apatch.spec import parse_spec_file, resolve_requirement
from apatch.spec_ownership import authorize_spec_owned_needles
from apatch.spec_executor import execute_next_workspace
from apatch.trust_identity import _PemKeyProvider

SPEC = "SPEC-METADATA-FIXTURE-1"
REL = "docs/specs/" + SPEC + ".md"
HEADER = "## R0 RFP traceability gate (meta)\n\n"


@pytest.fixture(autouse=True)
def isolated_lane_environment(monkeypatch):
    for name in ("APATCH_LANE", "APATCH_LANE_ID"):
        monkeypatch.delenv(name, raising=False)


def make(root, *, bind=True, r0="R0", owns=None):
    root.mkdir(parents=True, exist_ok=True)
    header = HEADER.replace("R0", r0, 1)
    declaration = "" if owns is None else "owns: " + owns + "\n\n"
    body = (
        "# " + SPEC + " — Disposable metadata contract\n\n"
        "> **apatch artifact:** `spec:" + SPEC + "`\n"
        "> **ownership mode:** strict\n\n" + header + declaration +
        "| RFP id | SPEC Rk | Disposition |\n|---|---|---|\n"
        "| META-1 | R1 | covered |\n\n"
        "(verify: python3 verify_metadata.py)\n\n"
        "## R1 Runtime\n\nowns: src/feature.py\n\n"
        "(verify: python3 verify_feature.py)\n"
    )
    path = root / REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src/feature.py").write_text("value = 6\n", encoding="utf-8")
    (root / "verify_metadata.py").write_text(
        "from pathlib import Path\n"
        "p = Path(" + repr(REL) + ")\n"
        "assert " + repr("owns: " + REL) + " in p.read_text()\n", encoding="utf-8")
    (root / "verify_feature.py").write_text(
        "from pathlib import Path\n"
        "assert Path('src/feature.py').read_text() == 'value = 42\\n'\n",
        encoding="utf-8")
    if bind:
        bind_requirement(root, SPEC + "#" + r0)
    return root


def bind_requirement(root, token=SPEC + "#R0", *, kind="spec", digest=None):
    resolved = resolve_requirement(str(root), token)
    assert resolved["ok"] is True
    save_session_state(str(root), {
        "session_id": "explicit-unit-state-not-native-owner-proof",
        "intent": "test exact self-declaration",
        "artifacts": [{"kind": kind, "id": token,
            "content_hash": digest if digest is not None else resolved["content_hash"]}],
        "ended_at": None,
    }, force=True)


def needle():
    return {"action": "replace", "target_file": REL, "find_text": HEADER,
            "replace_text": HEADER + "owns: " + REL + "\n\n"}


def gate(root, needles=None, channel="apatch_execute_next"):
    return authorize_spec_owned_needles(str(root), needles if needles is not None else [needle()],
        created_by_tool=channel)


def require_correction(root):
    result = gate(root)
    assert result["ok"] is True, (
        "Exact R0 cannot restore only its missing self declaration; "
        "this is the reproducible Core metadata bootstrap deadlock: " + repr(result))
    return result


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def test_positive_authorization_is_read_only_and_exact(tmp_path):
    root = make(tmp_path / "positive")
    before = files(root)
    result = require_correction(root)
    assert files(root) == before
    assert result["requirement_token"] == SPEC + "#R0"
    assert {r["path"] for r in result["owned"]} == {REL}
    assert {r["spec"] for r in result["owned"]} == {SPEC}


@pytest.mark.parametrize("change", [
    "source_sibling", "foreign_spec", "verify_change", "other_requirement_change",
    "strict_removal", "foreign_owns", "wildcard_owns", "extra_owner",
    "delete", "rename", "chmod", "create", "passthrough", "duplicate_target",
    "whole_file_rewrite", "insert_section", "unknown_field", "r0_and_verify_same_needle",
])
def test_negative_never_turns_into_document_or_runtime_authority(tmp_path, change):
    root = make(tmp_path / change)
    require_correction(root)  # A permanently denying implementation cannot satisfy these judges.
    rows = [needle()]
    row = rows[0]
    if change == "source_sibling":
        rows.append({"action": "replace", "target_file": "src/feature.py",
                     "find_text": "value = 6", "replace_text": "value = 42"})
    elif change == "foreign_spec":
        row["target_file"] = "docs/specs/SPEC-FOREIGN-1.md"
    elif change == "verify_change":
        row["find_text"] = "(verify: python3 verify_metadata.py)"
        row["replace_text"] = "(verify: true)"
    elif change == "other_requirement_change":
        row["find_text"] = "owns: src/feature.py"
        row["replace_text"] = "owns: src/**"
    elif change == "strict_removal":
        row["find_text"] = "> **ownership mode:** strict"
        row["replace_text"] = ""
    elif change == "foreign_owns":
        row["replace_text"] = HEADER + "owns: src/feature.py\n\n"
    elif change == "wildcard_owns":
        row["replace_text"] = HEADER + "owns: docs/specs/**\n\n"
    elif change == "extra_owner":
        row["replace_text"] = HEADER + "owns: " + REL + ", src/feature.py\n\n"
    elif change == "delete":
        rows = [{"action": "delete", "target_file": REL}]
    elif change == "rename":
        rows = [{"action": "rename", "source_file": REL, "target_file": "docs/specs/SPEC-RENAMED-1.md"}]
    elif change == "chmod":
        rows = [{"action": "chmod", "target_file": REL, "mode": "777"}]
    elif change == "create":
        rows = [{"action": "create", "target_file": REL, "content": (root / REL).read_text()}]
    elif change == "passthrough":
        rows = [{"tool_calls": [{"name": "apply_patch", "arguments": {"patch": "*** Delete File: " + REL}}]}]
    elif change == "duplicate_target":
        rows.append(copy.deepcopy(row))
    elif change == "whole_file_rewrite":
        row["find_text"] = (root / REL).read_text()
        row["replace_text"] = row["find_text"].replace(HEADER, HEADER + "owns: " + REL + "\n\n")
    elif change == "unknown_field":
        row["match_mode"] = "regex"
    elif change == "r0_and_verify_same_needle":
        row["replace_text"] += "(verify: true)\n"
    elif change == "insert_section":
        rows = [{"action": "insert_section", "target_file": REL, "before": "R1",
                 "content": "## R2 Hidden\nowns: src/**\n(verify: true)\n"}]
    before = files(root)
    assert gate(root, rows)["ok"] is False
    assert files(root) == before


@pytest.mark.parametrize("change", [
    "generic_channel", "plain_artifact", "bootstrap_artifact", "wrong_requirement",
    "stale_hash", "ended_session", "active_profile", "profile_symlink",
    "doc_symlink", "doc_hardlink", "already_declared", "not_r0",
    "crlf_document", "dangling_profile_symlink",
])
def test_boundary_requires_live_exact_r0_in_unfrozen_workspace(tmp_path, change):
    root = make(tmp_path / change)
    require_correction(root)
    channel = "apatch_execute_next"
    if change == "generic_channel":
        channel = "apatch_generate_batch"
    elif change == "plain_artifact":
        state = json.loads((root / ".apatch/session_state.json").read_text())
        state["artifacts"] = [{"kind": "spec", "id": SPEC}]
        save_session_state(str(root), state, force=True)
    elif change == "bootstrap_artifact":
        bind_requirement(root, kind="spec-bootstrap")
    elif change == "wrong_requirement":
        bind_requirement(root, SPEC + "#R1")
    elif change == "stale_hash":
        bind_requirement(root, digest="sha256:" + "0" * 64)
    elif change == "ended_session":
        state = json.loads((root / ".apatch/session_state.json").read_text())
        state["ended_at"] = "2026-10-05T00:00:00Z"
        save_session_state(str(root), state, force=True)
    elif change in ("active_profile", "profile_symlink"):
        profile = root / ".apatch/sdd_verification_contract.json"
        if change == "active_profile":
            profile.write_text('{"fixture_only":true}\n')
        else:
            external = tmp_path / "foreign-profile.json"
            external.write_text('{"fixture_only":true}\n')
            profile.symlink_to(external)
    elif change == "doc_symlink":
        external = tmp_path / "foreign-spec.md"
        (root / REL).rename(external)
        (root / REL).symlink_to(external)
    elif change == "doc_hardlink":
        external = tmp_path / "foreign-linked-spec.md"
        os.link(root / REL, external)
    elif change == "crlf_document":
        p = root / REL
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        bind_requirement(root)
    elif change == "dangling_profile_symlink":
        (root / ".apatch/sdd_verification_contract.json").symlink_to(tmp_path / "missing-profile.json")
    elif change == "already_declared":
        p = root / REL
        p.write_text(p.read_text().replace(HEADER, HEADER + "owns: tests/**\n\n"))
        bind_requirement(root)
    elif change == "not_r0":
        p = root / REL
        p.write_text(p.read_text().replace(HEADER, HEADER.replace("R0", "R2", 1)))
        bind_requirement(root, SPEC + "#R2")
    before = files(root)
    assert gate(root, channel=channel)["ok"] is False
    assert files(root) == before


def test_regression_normal_runtime_binding_still_rejects_extra_targets(tmp_path):
    root = make(tmp_path / "regression")
    bind_requirement(root, SPEC + "#R1")
    source = {"action": "replace", "target_file": "src/feature.py",
              "find_text": "value = 6", "replace_text": "value = 42"}
    assert gate(root, [source])["ok"] is True
    extra = {"action": "create", "target_file": "src/hidden.py", "content": "pass\n"}
    before = files(root)
    rejected = gate(root, [source, extra])
    assert rejected["ok"] is False and rejected["generation_started"] is False
    assert rejected["rejected_targets"] == ["src/hidden.py"]
    assert files(root) == before


def native_identity(root, tmp_path, monkeypatch):
    key = tmp_path / "existing-disposable-identity.pem"
    private = Ed25519PrivateKey.generate()  # Fixture provisioning only; not production key generation.
    key.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    key.chmod(0o600)
    for name in ("APATCH_AGENT_SIGN_CMD", "APATCH_AGENT_PUBKEY", "APATCH_AGENT_CERT",
                 "APATCH_PLATFORM_URL", "APATCH_PLATFORM_TOKEN", "APATCH_CANONICAL_RUNTIME",
                 "APATCH_POLICY_DENY", "APATCH_LANE", "APATCH_LANE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APATCH_AGENT_ID", "disposable-metadata-fixture")
    monkeypatch.setenv("APATCH_AGENT_KEY", str(key))
    monkeypatch.setenv("APATCH_KEY_BACKEND", "pem")
    chain = root / ".trustchain"
    chain.mkdir()
    tc = TrustChain(TrustChainConfig(enable_chain=True, chain_storage="file",
        chain_dir=str(chain), key_provider=_PemKeyProvider(str(key), "disposable-metadata-fixture"),
        enable_pki=False))
    tc.sign("fixture_genesis", {"synthetic_identity": True})
    return key, key.read_bytes(), private.public_key().public_bytes_raw()


def native_finish(root, requirement, needles):
    result = execute_next_workspace(str(root), spec=SPEC, requirement=SPEC + "#" + requirement,
        needles=needles, skip_lint=True, verify_deferred=True)
    assert result["ok"] is True, repr(result)
    if result.get("execution_phase") != "complete":
        result = execute_next_workspace(str(root), spec=SPEC, requirement=SPEC + "#" + requirement,
            finalize=True, skip_lint=True,
            completion_summary="Disposable native qualification; no actual user authority or HOST admission.")
    assert result["ok"] is True and result["execution_phase"] == "complete", repr(result)
    return result


def verify_native_chain(root, public, key, original_key):
    rows = []
    verifier = TrustChainVerifier(base64.b64encode(public).decode(),
        "disposable-metadata-fixture", max_age_seconds=None)
    for path in sorted((root / ".trustchain/objects").glob("op_*.json")):
        raw = json.loads(path.read_bytes())
        value = raw.get("value", raw)
        assert verify_record_signature(value, verifier) is True
        rows.append(value)
    assert key.read_bytes() == original_key
    assert any(row.get("tool") == "apatch_attest" for row in rows)
    return rows


def test_regression_real_native_runtime_fixture_is_qualified(tmp_path, monkeypatch):
    root = make(tmp_path / "native-runtime", bind=False)
    key, before, public = native_identity(root, tmp_path, monkeypatch)
    original_doc = (root / REL).read_bytes()
    source = {"action": "replace", "target_file": "src/feature.py",
              "find_text": "value = 6", "replace_text": "value = 42"}
    native_finish(root, "R1", [source])
    assert (root / "src/feature.py").read_text() == "value = 42\n"
    assert (root / REL).read_bytes() == original_doc
    verify_native_chain(root, public, key, before)


def test_positive_real_native_r0_cycle_changes_only_self_declaration(tmp_path, monkeypatch):
    probe = make(tmp_path / "preflight")
    require_correction(probe)
    root = make(tmp_path / "native-r0", bind=False)
    key, before_key, public = native_identity(root, tmp_path, monkeypatch)
    original_doc = (root / REL).read_text()
    source = (root / "src/feature.py").read_bytes()
    native_finish(root, "R0", [needle()])
    assert (root / REL).read_text() == original_doc.replace(
        HEADER, HEADER + "owns: " + REL + "\n\n", 1)
    assert (root / "src/feature.py").read_bytes() == source
    assert not (root / ".apatch/sdd_verification_contract.json").exists()
    parsed = parse_spec_file(str(root / REL))
    assert next(r for r in parsed.requirements if r.id == "R0").owns == (REL,)
    verify_native_chain(root, public, key, before_key)
