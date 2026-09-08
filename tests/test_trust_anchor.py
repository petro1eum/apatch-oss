"""Trust-anchor wiring: apatch signs its ledger with the enrolled identity.

Covers RFP-005 §5.2 — the enrolled Ed25519 key (whose leaf cert chains to the
TrustChain root) must be the key that signs apatch's local ledger, instead of
an ephemeral self-signed dev key.
"""

import base64

from apatch.trust_identity import anchor_status, load_local_identity
from tests.trustchain_compat import (
    require_trustchain_key_provider,
    require_trustchain_public_key_binding,
)


def _write_enrolled_key(tmp_path):
    """Generate an Ed25519 PEM key like an enrolled agent.key, return (path, pub_b64)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    sk = Ed25519PrivateKey.generate()
    pem = sk.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    key_file = tmp_path / "agent.key"
    key_file.write_bytes(pem)
    pub_b64 = base64.b64encode(sk.public_key().public_bytes_raw()).decode("ascii")
    return str(key_file), pub_b64


def _clear_identity_env(monkeypatch):
    for var in (
        "APATCH_AGENT_ID",
        "APATCH_AGENT_KEY",
        "APATCH_AGENT_CERT",
        "APATCH_KEY_BACKEND",
        "APATCH_AGENT_SIGN_CMD",
        "APATCH_AGENT_PUBKEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_anchor_status_ephemeral_by_default(monkeypatch):
    _clear_identity_env(monkeypatch)
    status = anchor_status()
    assert status["level"] == "ephemeral"
    assert status["secure"] is False
    assert status["agent_id"] is None
    assert status["hint"]


def test_load_identity_none_without_env(monkeypatch):
    _clear_identity_env(monkeypatch)
    assert load_local_identity() is None


def test_load_identity_provider_seed_matches(tmp_path, monkeypatch):
    key_path, pub_b64 = _write_enrolled_key(tmp_path)
    monkeypatch.setenv("APATCH_AGENT_ID", "apatch-test-agent")
    monkeypatch.setenv("APATCH_AGENT_KEY", key_path)

    ident = load_local_identity()
    assert ident is not None
    assert ident.agent_id == "apatch-test-agent"
    # Provider exposes the enrolled public key (binds ledger signatures to cert).
    assert base64.b64encode(ident.key_provider.get_public_key()).decode() == pub_b64
    assert ident.key_provider.get_key_id() == "apatch-test-agent"


def test_anchor_status_ca_issued_with_env(tmp_path, monkeypatch):
    key_path, _ = _write_enrolled_key(tmp_path)
    monkeypatch.setenv("APATCH_AGENT_ID", "apatch-test-agent")
    monkeypatch.setenv("APATCH_AGENT_KEY", key_path)

    status = anchor_status()
    assert status["level"] == "ca-issued"
    assert status["secure"] is True
    assert status["agent_id"] == "apatch-test-agent"


def test_tc_config_routes_enrolled_key_into_signer(tmp_path, monkeypatch):
    """The wired TrustChainConfig must sign with the enrolled key, not ephemeral."""
    require_trustchain_key_provider()
    from trustchain import TrustChain

    from apatch.trustchain_helper import TrustChainHelper

    (tmp_path / ".trustchain").mkdir()
    key_path, pub_b64 = _write_enrolled_key(tmp_path)

    helper = TrustChainHelper(str(tmp_path), auto_init=False)

    # Ephemeral: no identity env → signer public key is NOT the enrolled key.
    _clear_identity_env(monkeypatch)
    eph_pub = TrustChain(helper._tc_config())._signer.get_public_key()
    assert eph_pub != pub_b64

    # Enrolled: identity env → signer public key IS the enrolled key.
    monkeypatch.setenv("APATCH_AGENT_ID", "apatch-test-agent")
    monkeypatch.setenv("APATCH_AGENT_KEY", key_path)
    enrolled_pub = TrustChain(helper._tc_config())._signer.get_public_key()
    assert enrolled_pub == pub_b64


def test_doctor_reports_trust_anchor(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    from apatch.doctor import run_doctor

    info = run_doctor(str(tmp_path))
    assert "trust_anchor" in info
    assert info["trust_anchor"]["level"] in ("ephemeral", "ca-issued")


def _issue_pki_bundle(tmp_path):
    """Build root→intermediate→leaf bound to a known key; return PEM paths + pub_b64."""
    require_trustchain_public_key_binding()
    from trustchain.v2.x509_pki import TrustChainCA

    key_path, pub_b64 = _write_enrolled_key(tmp_path)

    root = TrustChainCA.create_root_ca("Test Root")
    inter = root.issue_intermediate_ca()
    leaf = inter.issue_agent_cert("apatch-ci", public_key_b64=pub_b64)

    root_pem = tmp_path / "root-ca.pem"
    int_pem = tmp_path / "ca.pem"
    leaf_pem = tmp_path / "agent.crt"
    root_pem.write_text(root.certificate_pem)
    int_pem.write_text(inter.certificate_pem)
    leaf_pem.write_text(leaf.to_pem())
    return {
        "root": str(root_pem),
        "intermediate": str(int_pem),
        "leaf": str(leaf_pem),
        "key": key_path,
        "pub_b64": pub_b64,
    }


def test_verify_anchor_pem_roundtrip_ok(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    from apatch.trust_identity import verify_anchor

    b = _issue_pki_bundle(tmp_path)
    result = verify_anchor(
        root_ca=b["root"],
        intermediate=b["intermediate"],
        leaf_cert=b["leaf"],
        signer_key=b["key"],
    )
    assert result["ok"] is True
    assert result["chain_ok"] is True
    assert result["key_match"] is True
    assert result["leaf_public_key"] == b["pub_b64"]


def test_verify_anchor_wrong_root_fails(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    require_trustchain_public_key_binding()
    from trustchain.v2.x509_pki import TrustChainCA

    from apatch.trust_identity import verify_anchor

    b = _issue_pki_bundle(tmp_path)
    other_root = TrustChainCA.create_root_ca("Other Root")
    wrong_root = tmp_path / "wrong-root.pem"
    wrong_root.write_text(other_root.certificate_pem)

    result = verify_anchor(
        root_ca=str(wrong_root),
        intermediate=b["intermediate"],
        leaf_cert=b["leaf"],
        signer_key=b["key"],
    )
    assert result["ok"] is False
    assert result["chain_ok"] is False


def test_verify_anchor_key_mismatch_fails(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    from apatch.trust_identity import verify_anchor

    b = _issue_pki_bundle(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir(exist_ok=True)
    other_key, _ = _write_enrolled_key(other_dir)

    result = verify_anchor(
        root_ca=b["root"],
        intermediate=b["intermediate"],
        leaf_cert=b["leaf"],
        signer_key=other_key,
    )
    assert result["ok"] is False
    assert result["key_match"] is False


def test_verify_anchor_rejects_an_expired_leaf(tmp_path, monkeypatch):
    """A chain can verify cryptographically and still be worthless today."""
    _clear_identity_env(monkeypatch)
    from datetime import datetime, timedelta, timezone

    from apatch.trust_identity import verify_anchor

    b = _issue_pki_bundle(tmp_path)
    result = verify_anchor(
        root_ca=b["root"], intermediate=b["intermediate"], leaf_cert=b["leaf"],
        signer_key=b["key"], now=datetime.now(timezone.utc) + timedelta(days=365 * 20),
    )
    assert result["chain_ok"] is True
    assert result["validity_ok"] is False
    assert result["ok"] is False
    assert any("expired at" in m for m in result["errors"]), result["errors"]


def test_verify_anchor_rejects_a_certificate_that_is_not_valid_yet(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    from datetime import datetime, timedelta, timezone

    from apatch.trust_identity import verify_anchor

    b = _issue_pki_bundle(tmp_path)
    result = verify_anchor(
        root_ca=b["root"], intermediate=b["intermediate"], leaf_cert=b["leaf"],
        signer_key=b["key"], now=datetime.now(timezone.utc) - timedelta(days=365 * 20),
    )
    assert result["validity_ok"] is False
    assert result["ok"] is False
    assert any("not valid until" in m for m in result["errors"]), result["errors"]


def test_verify_anchor_reports_validity_inside_the_window(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    from apatch.trust_identity import verify_anchor

    b = _issue_pki_bundle(tmp_path)
    result = verify_anchor(
        root_ca=b["root"], intermediate=b["intermediate"], leaf_cert=b["leaf"], signer_key=b["key"],
    )
    assert result["validity_ok"] is True
    assert result["ok"] is True


def test_verify_anchor_missing_inputs(monkeypatch):
    _clear_identity_env(monkeypatch)
    monkeypatch.delenv("APATCH_PLATFORM_URL", raising=False)
    from apatch.trust_identity import verify_anchor

    result = verify_anchor()
    assert result["ok"] is False
    assert result["errors"]


def test_enroll_agent_requires_tc_cli(tmp_path, monkeypatch):
    import shutil

    from apatch.trust_identity import enroll_agent

    monkeypatch.setattr(shutil, "which", lambda _c: None)
    result = enroll_agent(
        invitation="tok",
        platform_url="https://example.invalid",
        out_dir=str(tmp_path / "id"),
    )
    assert result["ok"] is False
    assert "tc CLI" in result["error"]
