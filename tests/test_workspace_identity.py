"""Workspace-scoped agent identity (.apatch/agent-identity.json)."""

import base64
import json
import sys

from apatch.trust_identity import anchor_status, load_local_identity
from tests.test_trust_anchor import _clear_identity_env, _write_enrolled_key


def _write_command_signer(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private_key = ed25519.Ed25519PrivateKey.generate()
    key_path = tmp_path / "command-signer.key"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_key = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    script = tmp_path / "command-signer.py"
    script.write_text(
        "import base64, sys\n"
        "from cryptography.hazmat.primitives.serialization import load_pem_private_key\n"
        f"key = load_pem_private_key(open({str(key_path)!r}, 'rb').read(), None)\n"
        "sys.stdout.write(base64.b64encode(key.sign(sys.stdin.buffer.read())).decode())\n",
        encoding="utf-8",
    )
    return f"{sys.executable} {script}", public_key


def test_load_identity_from_workspace_file(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    key_path, pub_b64 = _write_enrolled_key(tmp_path)
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "agent-identity.json").write_text(
        json.dumps({"agent_id": "ws-agent", "key": key_path}),
        encoding="utf-8",
    )

    ident = load_local_identity(str(tmp_path))
    assert ident is not None
    assert ident.agent_id == "ws-agent"

    status = anchor_status(str(tmp_path))
    assert status["level"] == "ca-issued"
    assert status["agent_id"] == "ws-agent"


def test_env_overrides_workspace_file(tmp_path, monkeypatch):
    key_path, _ = _write_enrolled_key(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other_key, _ = _write_enrolled_key(other_dir)
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "agent-identity.json").write_text(
        json.dumps({"agent_id": "ws-agent", "key": key_path}),
        encoding="utf-8",
    )
    monkeypatch.setenv("APATCH_AGENT_ID", "env-agent")
    monkeypatch.setenv("APATCH_AGENT_KEY", other_key)

    ident = load_local_identity(str(tmp_path))
    assert ident is not None
    assert ident.agent_id == "env-agent"


def test_load_command_identity_from_workspace_file(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    for var in ("APATCH_KEY_BACKEND", "APATCH_AGENT_SIGN_CMD", "APATCH_AGENT_PUBKEY"):
        monkeypatch.delenv(var, raising=False)
    sign_cmd, public_key = _write_command_signer(tmp_path)
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "agent-identity.json").write_text(
        json.dumps(
            {
                "agent_id": "ws-command-agent",
                "key_backend": "command",
                "sign_cmd": sign_cmd,
                "public_key": public_key,
            }
        ),
        encoding="utf-8",
    )

    ident = load_local_identity(str(tmp_path))

    assert ident is not None
    assert ident.agent_id == "ws-command-agent"
    assert ident.backend == "command"
    assert ident.key_path is None
    payload = b"workspace command backend"
    signature = ident.key_provider.sign(payload)
    assert ident.key_provider.verify(payload, signature) is True
    assert base64.b64encode(ident.key_provider.get_public_key()).decode("ascii") == public_key


def test_env_command_fields_override_workspace_command_fields(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    ws_sign_cmd, ws_public_key = _write_command_signer(tmp_path)
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    env_sign_cmd, env_public_key = _write_command_signer(env_dir)
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "agent-identity.json").write_text(
        json.dumps(
            {
                "agent_id": "ws-command-agent",
                "key_backend": "command",
                "sign_cmd": ws_sign_cmd,
                "public_key": ws_public_key,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APATCH_AGENT_ID", "env-command-agent")
    monkeypatch.setenv("APATCH_KEY_BACKEND", "command")
    monkeypatch.setenv("APATCH_AGENT_SIGN_CMD", env_sign_cmd)
    monkeypatch.setenv("APATCH_AGENT_PUBKEY", env_public_key)

    ident = load_local_identity(str(tmp_path))

    assert ident is not None
    assert ident.agent_id == "env-command-agent"
    assert ident.backend == "command"
    assert (
        base64.b64encode(ident.key_provider.get_public_key()).decode("ascii")
        == env_public_key
    )


def test_env_pem_key_overrides_workspace_command_backend(tmp_path, monkeypatch):
    _clear_identity_env(monkeypatch)
    ws_sign_cmd, ws_public_key = _write_command_signer(tmp_path)
    pem_dir = tmp_path / "pem"
    pem_dir.mkdir()
    env_key, env_public_key = _write_enrolled_key(pem_dir)
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "agent-identity.json").write_text(
        json.dumps(
            {
                "agent_id": "ws-command-agent",
                "key_backend": "command",
                "sign_cmd": ws_sign_cmd,
                "public_key": ws_public_key,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APATCH_AGENT_ID", "env-pem-agent")
    monkeypatch.setenv("APATCH_AGENT_KEY", env_key)

    ident = load_local_identity(str(tmp_path))

    assert ident is not None
    assert ident.agent_id == "env-pem-agent"
    assert ident.backend == "pem"
    assert ident.key_path == env_key
    assert (
        base64.b64encode(ident.key_provider.get_public_key()).decode("ascii")
        == env_public_key
    )
