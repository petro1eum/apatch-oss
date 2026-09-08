"""Avatar Compiler perf/scope fix: index reuse + scope field (RFP-025 follow-up)."""


def _entries():
    return [
        {"id": "i1", "tool_id": "apatch", "timestamp": "2026-06-01T00:00:00",
         "payload": {"action": "engineering_pipeline", "intent": "SPEC-FOO-1#R1",
                     "artifacts": [{"kind": "spec", "id": "SPEC-FOO-1#R1"}]}},
        {"id": "m1", "tool_id": "apatch", "timestamp": "2026-06-01T00:01:00",
         "payload": {"action": "apply", "applied_patches": 1}},
        {"id": "a1", "tool_id": "apatch_attest", "timestamp": "2026-06-02T00:00:00",
         "payload": {"action": "attest"}},
    ]


def test_asset_summary_from_index_matches_and_scoped(monkeypatch):
    monkeypatch.setattr("apatch.contribution.resolve_identity", lambda *_a, **_k: {
        "key_id": "k", "cert_fingerprint": None, "agent_id": "a",
        "ca": "platform", "trust_level": "attested"})
    from apatch import avatar_compiler as A
    from apatch.traceability import build_traceability_index

    idx = build_traceability_index(_entries())
    s = A.asset_summary_from_index(idx, ".")
    assert s["scope"] == "ledger"
    assert s["artifact_count"] == 1
    assert s["spec_ids"] == ["SPEC-FOO-1"]

    # index-reuse path equals the full ledger-read path (no behavioural drift)
    from apatch.trustchain_helper import TrustChainHelper
    monkeypatch.setattr(TrustChainHelper, "iter_ledger_entries", lambda self: _entries())
    assert A.build_asset_summary(".") == s