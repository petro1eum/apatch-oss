"""Pluggable key backend — external (hard-KMS) command signer (RFP-005 §0.3).

Exercises the full path: apatch ``CommandKeyProvider`` (seed never enters the
process) → TrustChain's delegating ``Signer.from_provider`` → ledger / policy
signatures, plus the env-driven backend resolution.
"""

import base64
import json
import sys

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization
from tests.trustchain_compat import (
    require_trustchain_key_provider,
    require_trustchain_kms,
)
from cryptography.hazmat.primitives.asymmetric import ed25519


def _make_external_signer(tmp_path):
    """Create an ed25519 key + a stdin→base64-sig CLI that 'holds' it (fake HSM).

    Returns (sign_cmd, pub_b64).
    """
    priv = ed25519.Ed25519PrivateKey.generate()
    key_pem = tmp_path / "hsm_key.pem"
    key_pem.write_bytes(
        priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode("ascii")

    script = tmp_path / "sign.py"
    script.write_text(
        "import sys, base64\n"
        "from cryptography.hazmat.primitives.serialization import load_pem_private_key\n"
        f"k = load_pem_private_key(open({str(key_pem)!r}, 'rb').read(), password=None)\n"
        "data = sys.stdin.buffer.read()\n"
        "sys.stdout.write(base64.b64encode(k.sign(data)).decode())\n",
        encoding="utf-8",
    )
    sign_cmd = f"{sys.executable} {script}"
    return sign_cmd, pub_b64


def _set_command_backend(tmp_path, monkeypatch):
    sign_cmd, pub_b64 = _make_external_signer(tmp_path)
    monkeypatch.setenv("APATCH_KEY_BACKEND", "command")
    monkeypatch.setenv("APATCH_AGENT_SIGN_CMD", sign_cmd)
    monkeypatch.setenv("APATCH_AGENT_PUBKEY", pub_b64)
    monkeypatch.setenv("APATCH_AGENT_ID", "apatch-hsm-agent")
    for v in ("APATCH_AGENT_KEY", "APATCH_AGENT_CERT"):
        monkeypatch.delenv(v, raising=False)
    return pub_b64


def test_command_provider_signs_and_verifies(tmp_path, monkeypatch):
    from apatch.trust_identity import CommandKeyProvider

    sign_cmd, pub_b64 = _make_external_signer(tmp_path)
    prov = CommandKeyProvider(sign_cmd, pub_b64, key_id="k1")

    data = b"governed mutation payload"
    sig = prov.sign(data)
    assert prov.verify(data, sig) is True
    assert prov.verify(data + b"x", sig) is False
    assert base64.b64encode(prov.get_public_key()).decode() == pub_b64


def test_command_provider_get_seed_raises(tmp_path):
    require_trustchain_kms()
    from trustchain.kms import KeyProviderError

    from apatch.trust_identity import CommandKeyProvider

    sign_cmd, pub_b64 = _make_external_signer(tmp_path)
    prov = CommandKeyProvider(sign_cmd, pub_b64, key_id="k1")
    with pytest.raises(KeyProviderError, match="hard-KMS"):
        prov.get_seed()


def test_load_identity_uses_command_backend(tmp_path, monkeypatch):
    from apatch.trust_identity import load_local_identity

    _set_command_backend(tmp_path, monkeypatch)
    ident = load_local_identity()
    assert ident is not None
    assert ident.backend == "command"
    assert ident.key_path is None
    assert ident.agent_id == "apatch-hsm-agent"


def test_anchor_status_reports_command_backend(tmp_path, monkeypatch):
    from apatch.trust_identity import anchor_status

    _set_command_backend(tmp_path, monkeypatch)
    st = anchor_status()
    assert st["level"] == "ca-issued"
    assert st["backend"] == "command"


def test_tc_config_routes_command_provider_into_delegating_signer(tmp_path, monkeypatch):
    """The ledger signer delegates to the external command; no in-process seed."""
    require_trustchain_key_provider()
    from trustchain import TrustChain

    from apatch.trust_identity import load_local_identity
    from apatch.trustchain_helper import TrustChainHelper

    pub_b64 = _set_command_backend(tmp_path, monkeypatch)
    (tmp_path / ".trustchain").mkdir()
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    signer = TrustChain(helper._tc_config())._signer

    assert signer._private_key is None  # hard-KMS: seed never in-process
    assert signer._provider is not None
    assert signer.get_public_key() == pub_b64

    resp = signer.sign("apatch.test", {"k": 1})
    assert signer.verify(resp) is True
    # Sanity: provider is the apatch CommandKeyProvider.
    ident = load_local_identity()
    assert signer._provider.get_public_key() == ident.key_provider.get_public_key()


def test_policy_sign_verify_with_command_backend(tmp_path, monkeypatch):
    from apatch.policy_lock import sign_policy, verify_policy

    d = tmp_path / ".apatch"
    d.mkdir()
    (d / "sandbox.json").write_text(json.dumps({"mode": "enforce"}), encoding="utf-8")
    (d / "enforcement.json").write_text(json.dumps({"mode": "strict"}), encoding="utf-8")
    _set_command_backend(tmp_path, monkeypatch)

    signed = sign_policy(str(tmp_path))
    assert signed["ok"] is True
    res = verify_policy(str(tmp_path))
    assert res["ok"] is True
    assert res["secure"] is True
