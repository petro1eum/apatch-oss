"""Ledger actor resolution (signed_by / key_id)."""

import json

from apatch.ledger_actor import enrich_payload_with_actor, ledger_signed_by


def test_ledger_signed_by_from_envelope_key_id():
    row = {"key_id": "apatch-edcher-prod", "payload": {}}
    assert ledger_signed_by(row) == "apatch-edcher-prod"


def test_ledger_signed_by_from_payload_fallback():
    row = {"payload": {"signed_by": "hc-operator-uk-001"}}
    assert ledger_signed_by(row) == "hc-operator-uk-001"


def test_enrich_payload_with_actor():
    out = enrich_payload_with_actor({"action": "apply"}, agent_id="agent-cn")
    assert out["signed_by"] == "agent-cn"
    assert out["agent_id"] == "agent-cn"
    assert out["key_id"] == "agent-cn"


def test_traceability_op_summary_includes_signed_by(tmp_path):
    from apatch.trustchain_helper import TrustChainHelper

    tc_dir = tmp_path / ".trustchain" / "objects"
    tc_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    payload = {
        "value": {
            "tool_id": "apatch_attest",
            "data": {"intent": "test", "artifacts": [{"kind": "spec", "id": "SPEC-1"}]},
            "signature": "sig0",
            "id": "op-attest",
            "key_id": "apatch-edcher-prod",
        }
    }
    (tc_dir / "entry0.json").write_text(__import__("json").dumps(payload), encoding="utf-8")
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    report = helper.artifact_coverage(artifact="spec:SPEC-1")
    attest = report["attestations"][0]
    assert attest["signed_by"] == "apatch-edcher-prod"


def test_commit_enriches_actor_when_enrolled(tmp_path, monkeypatch):
    from tests.test_trust_anchor import _clear_identity_env, _write_enrolled_key
    from apatch.session_state import save_session_state
    from apatch.trustchain_helper import TrustChainHelper

    _clear_identity_env(monkeypatch)
    key_path, _ = _write_enrolled_key(tmp_path)
    monkeypatch.setenv("APATCH_AGENT_ID", "agent-cn")
    monkeypatch.setenv("APATCH_AGENT_KEY", str(key_path))
    save_session_state(str(tmp_path), {"session_id": "test-sess", "intent": "apply"})
    (tmp_path / ".trustchain" / "objects").mkdir(parents=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    enriched = helper._enrich_payload_from_governed_session({"action": "apply"})
    assert enriched.get("signed_by") == "agent-cn"
    assert enriched.get("key_id") == "agent-cn"


def test_parse_ledger_object_key_id():
    from apatch.trustchain_helper import TrustChainHelper

    row = TrustChainHelper._parse_ledger_object(
        {
            "value": {
                "tool_id": "apatch",
                "data": {"action": "apply"},
                "key_id": "apatch-edcher-prod",
                "algorithm": "Ed25519",
            }
        }
    )
    assert row["key_id"] == "apatch-edcher-prod"
    assert row["algorithm"] == "Ed25519"
    # R4 traceability covered by test_traceability_op_summary_includes_signed_by


def test_intent_history_signed_by(tmp_path):
    from apatch.trustchain_helper import TrustChainHelper

    tc_dir = tmp_path / ".trustchain" / "objects"
    tc_dir.mkdir(parents=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    payload = {
        "value": {
            "tool_id": "apatch",
            "data": {
                "intent": "billing",
                "action": "engineering_pipeline",
                "artifacts": [{"kind": "spec", "id": "SPEC-1"}],
            },
            "key_id": "apatch-edcher-prod",
            "id": "op-intent",
            "signature": "sig0",
        }
    }
    (tc_dir / "entry0.json").write_text(json.dumps(payload), encoding="utf-8")
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    hist = helper.list_intent_history(artifact="spec:SPEC-1")
    assert hist["entries"][0]["signed_by"] == "apatch-edcher-prod"


def test_resolve_op_id_signed_by(tmp_path):
    from apatch.trustchain_helper import TrustChainHelper

    tc_dir = tmp_path / ".trustchain" / "objects"
    tc_dir.mkdir(parents=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    payload = {
        "value": {
            "tool_id": "apatch_attest",
            "data": {"intent": "test", "artifacts": [{"kind": "spec", "id": "SPEC-6"}]},
            "signature": "sig6",
            "id": "op-attest-6",
            "key_id": "apatch-edcher-prod",
        }
    }
    (tc_dir / "entry0.json").write_text(json.dumps(payload), encoding="utf-8")
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    resolved = helper.resolve_op_id("op-attest-6")
    assert resolved["signed_by"] == "apatch-edcher-prod"


def test_doctor_surfaces_enrolled_agent_id(tmp_path, monkeypatch):
    from apatch.doctor import run_doctor
    from tests.test_trust_anchor import _clear_identity_env, _write_enrolled_key

    _clear_identity_env(monkeypatch)
    key_path, _ = _write_enrolled_key(tmp_path)
    monkeypatch.setenv("APATCH_AGENT_ID", "ledger-test-agent")
    monkeypatch.setenv("APATCH_AGENT_KEY", key_path)
    out = run_doctor(str(tmp_path))
    assert out.get("trust_anchor", {}).get("agent_id") == "ledger-test-agent"