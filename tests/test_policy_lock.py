"""Signed policy — tamper-evident monitor config (RFP-005 §0.3)."""

import json

from apatch.policy_lock import (
    compute_policy_manifest,
    policy_status,
    sign_policy,
    verify_policy,
)


def _enrolled(tmp_path, monkeypatch):
    """Wire an enrolled Ed25519 identity into the env (like an agent.key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    sk = Ed25519PrivateKey.generate()
    key_path = tmp_path / "agent.key"
    key_path.write_bytes(
        sk.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    monkeypatch.setenv("APATCH_AGENT_ID", "apatch-test-agent")
    monkeypatch.setenv("APATCH_AGENT_KEY", str(key_path))


def _write_policy_files(tmp_path):
    d = tmp_path / ".apatch"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sandbox.json").write_text(json.dumps({"version": 1, "mode": "enforce"}), encoding="utf-8")
    (d / "enforcement.json").write_text(json.dumps({"mode": "strict"}), encoding="utf-8")


def test_sign_requires_enrolled_identity(tmp_path, monkeypatch):
    for v in ("APATCH_AGENT_ID", "APATCH_AGENT_KEY", "APATCH_AGENT_CERT"):
        monkeypatch.delenv(v, raising=False)
    _write_policy_files(tmp_path)
    res = sign_policy(str(tmp_path))
    assert res["ok"] is False
    assert "enrolled identity" in res["error"]


def test_sign_then_verify_clean(tmp_path, monkeypatch):
    _write_policy_files(tmp_path)
    _enrolled(tmp_path, monkeypatch)
    signed = sign_policy(str(tmp_path))
    assert signed["ok"] is True
    assert (tmp_path / ".apatch" / "policy.lock.json").is_file()
    assert (tmp_path / ".apatch" / "policy.lock.json").stat().st_mode & 0o077 == 0

    res = verify_policy(str(tmp_path))
    assert res["ok"] is True
    assert res["signed"] is True
    assert res["signature_ok"] is True
    assert res["has_drift"] is False
    # signer is the enrolled key still in env → anchored
    assert res["secure"] is True


def test_verify_detects_drift(tmp_path, monkeypatch):
    _write_policy_files(tmp_path)
    _enrolled(tmp_path, monkeypatch)
    assert sign_policy(str(tmp_path))["ok"] is True

    # Tamper with a config file out-of-channel.
    (tmp_path / ".apatch" / "enforcement.json").write_text(
        json.dumps({"mode": "off"}), encoding="utf-8"
    )
    res = verify_policy(str(tmp_path))
    assert res["ok"] is False
    assert res["has_drift"] is True
    assert ".apatch/enforcement.json" in res["drift"]["changed"]


def test_verify_detects_added_and_removed(tmp_path, monkeypatch):
    _write_policy_files(tmp_path)
    _enrolled(tmp_path, monkeypatch)
    assert sign_policy(str(tmp_path))["ok"] is True

    # Remove one signed file, add a new control file.
    (tmp_path / ".apatch" / "enforcement.json").unlink()
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    (cursor / "hooks.json").write_text("{}", encoding="utf-8")

    res = verify_policy(str(tmp_path))
    assert res["ok"] is False
    assert ".apatch/enforcement.json" in res["drift"]["removed"]
    assert ".cursor/hooks.json" in res["drift"]["added"]


def test_verify_detects_bad_signature(tmp_path, monkeypatch):
    _write_policy_files(tmp_path)
    _enrolled(tmp_path, monkeypatch)
    assert sign_policy(str(tmp_path))["ok"] is True

    lock_path = tmp_path / ".apatch" / "policy.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["signature"] = "00" * 64  # forge
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    res = verify_policy(str(tmp_path))
    assert res["ok"] is False
    assert res["signature_ok"] is False


def test_remote_policy_is_signed_and_drift_detected(tmp_path, monkeypatch):
    _write_policy_files(tmp_path)
    remote_path = tmp_path / ".apatch" / "remote.json"
    remote_path.write_text(
        json.dumps({"version": 1, "targets": {"prod": {"host": "example.invalid"}}}),
        encoding="utf-8",
    )
    _enrolled(tmp_path, monkeypatch)

    signed = sign_policy(str(tmp_path))

    assert signed["ok"] is True
    assert ".apatch/remote.json" in signed["files"]
    manifest = compute_policy_manifest(str(tmp_path))
    assert ".apatch/remote.json" in manifest["files"]

    remote_path.write_text(
        json.dumps({"version": 1, "targets": {"prod": {"host": "tampered.invalid"}}}),
        encoding="utf-8",
    )
    result = verify_policy(str(tmp_path))
    assert result["ok"] is False
    assert ".apatch/remote.json" in result["drift"]["changed"]


def test_verify_unsigned_reports_not_signed(tmp_path):
    _write_policy_files(tmp_path)
    res = verify_policy(str(tmp_path))
    assert res["signed"] is False
    assert res["ok"] is False
    assert policy_status(str(tmp_path)) == {"signed": False, "ok": False, "secure": False}


def test_policy_lock_is_control_path():
    from apatch.sandbox import is_control_path

    assert is_control_path(".apatch/policy.lock.json")


def test_ci_gate_fails_on_policy_drift(tmp_path, monkeypatch):
    """RFP-005 §0.3: under enforcement, signed-then-drifted policy fails the gate."""
    import subprocess

    from apatch.enforcement import write_enforcement_config
    from apatch.sandbox import write_sandbox_config
    from apatch.sandbox_watch import run_sandbox_ci_gate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    write_sandbox_config(str(tmp_path))
    write_enforcement_config(str(tmp_path))
    _enrolled(tmp_path, monkeypatch)
    assert sign_policy(str(tmp_path))["ok"] is True

    # Clean gate first (notarization may skip; policy must be ok).
    pol = verify_policy(str(tmp_path))
    assert pol["ok"] is True

    # Drift the enforcement config out-of-channel.
    (tmp_path / ".apatch" / "enforcement.json").write_text(
        json.dumps({"mode": "strict", "governed_mode": "off"}), encoding="utf-8"
    )
    result = run_sandbox_ci_gate(str(tmp_path))
    assert result["ok"] is False
    assert result["reason"] == "policy drift"
