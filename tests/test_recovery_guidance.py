"""Embedded protocol_contract surfaces recovery routing to fresh MCP agents (RFP-027)."""
from apatch.agent_guidance import protocol_contract


def test_protocol_contract_has_recovery():
    rec = protocol_contract().get("recovery")
    assert isinstance(rec, dict)
    blob = str(rec)
    assert "apatch_resume_session" in blob
    assert "apatch_noop_attest" in blob
    assert "SESSION_ALREADY_COMPLETE" in blob
    assert "BACKUPS_PRUNED" in blob