"""Tests for apatch platform bridge (offline / no httpx)."""

from apatch.platform_client import platform_config_from_env, _canonical_log_envelope


def test_platform_config_missing_env(monkeypatch):
    monkeypatch.delenv("APATCH_PLATFORM_URL", raising=False)
    assert platform_config_from_env() is None


def test_platform_config_with_tenant(monkeypatch, tmp_path):
    key = tmp_path / "agent.key"
    key.write_text("dummy")
    monkeypatch.setenv("APATCH_PLATFORM_URL", "https://trust-chain.ai")
    monkeypatch.setenv("APATCH_AGENT_ID", "agent-1")
    monkeypatch.setenv("APATCH_AGENT_KEY", str(key))
    monkeypatch.setenv("APATCH_TENANT_ID", "acme")
    cfg = platform_config_from_env()
    assert cfg is not None
    assert cfg["tenant_id"] == "acme"
    body = _canonical_log_envelope(
        tool="apatch",
        agent_id="agent-1",
        data={"action": "strip"},
        timestamp=1.0,
        nonce="abc",
        parent_hash="root",
        metadata={"tenant_id": "acme"},
    )
    assert b"apatch" in body
    assert b"agent-1" in body
