"""CapabilityEvidence v2 bundle, signing and full re-derivation tests."""
import copy
import hashlib

import pytest


NOW = 1_783_862_400.0


class _Provider:
    def __init__(self, private, public):
        self.private = private
        self.public = public

    def sign(self, data):
        return self.private.sign(data)

    def get_public_key(self):
        return self.public


def _identity():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key_id = hashlib.sha256(public).hexdigest()[:32]
    return key_id, type("Identity", (), {"key_provider": _Provider(private, public)})()


def _episode(key_id, index, status):
    from tests.test_capability import _episode as capability_episode

    return capability_episode(index, avatar=key_id, status=status)


def _patch(monkeypatch):
    key_id, identity = _identity()
    episodes = [_episode(key_id, 1, "passed"), _episode(key_id, 2, "failed")]
    monkeypatch.setattr("apatch.trust_identity.load_local_identity", lambda _root: identity)
    monkeypatch.setattr(
        "apatch.contribution.resolve_identity",
        lambda _root: {"key_id": key_id, "trust_level": "attested"},
    )
    monkeypatch.setattr("apatch.episode.build_episodes", lambda *_a, **_k: copy.deepcopy(episodes))
    return key_id, episodes


def test_bundle_is_signed_shared_contract(monkeypatch, tmp_path):
    key_id, episodes = _patch(monkeypatch)
    from avatar_contract import CapabilityEvidenceBundle
    from apatch.avatar_evidence import build_evidence_bundle

    bundle = build_evidence_bundle(str(tmp_path), now_ts=NOW)
    CapabilityEvidenceBundle.from_wire(bundle, verify_signature=True)
    assert bundle["avatar_id"] == key_id
    assert bundle["episodes"] == episodes
    assert bundle["capability_estimates"][0]["success_estimate"]["mean"] == 0.5
    assert bundle["signature"]["algorithm"] == "ed25519"


def test_bundle_exports_qualified_distillate_not_rejected_activity(
    monkeypatch, tmp_path
):
    key_id, identity = _identity()
    eligible = _episode(key_id, 1, "passed")
    rejected = _episode(key_id, 2, "failed")
    rejected["eligible_for_capability"] = False
    rejected["exclusion_reasons"] = ["taxonomy_missing", "outcome_missing"]
    rejected["task"]["taxonomy_refs"] = []
    monkeypatch.setattr(
        "apatch.trust_identity.load_local_identity", lambda _root: identity
    )
    monkeypatch.setattr(
        "apatch.contribution.resolve_identity",
        lambda _root: {"key_id": key_id, "trust_level": "attested"},
    )
    monkeypatch.setattr(
        "apatch.episode.build_episodes",
        lambda *_a, **_k: copy.deepcopy([eligible, rejected]),
    )
    from apatch.avatar_evidence import build_evidence_bundle

    bundle = build_evidence_bundle(str(tmp_path), now_ts=NOW)

    assert bundle["evidence_scope"]["event_count"] == 2
    assert [item["episode_id"] for item in bundle["episodes"]] == [
        eligible["episode_id"]
    ]
    assert bundle["exclusions"] == {
        "count": 2,
        "reasons": {"outcome_missing": 1, "taxonomy_missing": 1},
    }


def test_bundle_exports_proxy_candidate_without_feeding_capability(
    monkeypatch, tmp_path
):
    key_id, identity = _identity()
    candidate = _episode(key_id, 1, "passed")
    candidate["outcome"] = {
        "status": "passed",
        "basis": "technical_gate",
        "observed_at": "2026-07-12T10:00:00+00:00",
        "reference": "event:proxy-1",
    }
    candidate["evidence_quality"]["outcome"] = "proxy"
    candidate["eligible_for_capability"] = False
    candidate["exclusion_reasons"] = ["outcome_not_independently_observed"]
    monkeypatch.setattr(
        "apatch.trust_identity.load_local_identity", lambda _root: identity
    )
    monkeypatch.setattr(
        "apatch.contribution.resolve_identity",
        lambda _root: {"key_id": key_id, "trust_level": "attested"},
    )
    monkeypatch.setattr(
        "apatch.episode.build_episodes",
        lambda *_a, **_k: copy.deepcopy([candidate]),
    )
    from apatch.avatar_evidence import build_evidence_bundle

    bundle = build_evidence_bundle(str(tmp_path), now_ts=NOW)

    assert bundle["episodes"] == [candidate]
    assert bundle["capability_estimates"] == []


def test_bundle_verify_rederives_every_field(monkeypatch, tmp_path):
    _patch(monkeypatch)
    from apatch.avatar_evidence import build_evidence_bundle, verify_evidence_bundle

    bundle = build_evidence_bundle(str(tmp_path), now_ts=NOW)
    assert verify_evidence_bundle(bundle, str(tmp_path))["ok"] is True
    tampered = copy.deepcopy(bundle)
    tampered["episodes"][0]["task"]["label"] = "forged"
    check = verify_evidence_bundle(tampered, str(tmp_path))
    assert check["ok"] is False
    assert check["errors"][0]["error"] == "contract_or_signature_invalid"


def test_attested_export_fails_closed_without_signer(monkeypatch, tmp_path):
    key_id, _identity_value = _identity()
    monkeypatch.setattr("apatch.trust_identity.load_local_identity", lambda _root: None)
    monkeypatch.setattr(
        "apatch.contribution.resolve_identity",
        lambda _root: {"key_id": key_id, "trust_level": "attested"},
    )
    monkeypatch.setattr("apatch.episode.build_episodes", lambda *_a, **_k: [])
    from apatch.avatar_evidence import EvidenceBarrierError, build_evidence_bundle

    with pytest.raises(EvidenceBarrierError, match="requires the enrolled"):
        build_evidence_bundle(str(tmp_path), now_ts=NOW)


def test_bundle_contains_no_economic_projection(monkeypatch, tmp_path):
    _patch(monkeypatch)
    from apatch.avatar_evidence import build_evidence_bundle

    blob = str(build_evidence_bundle(str(tmp_path), now_ts=NOW)).lower()
    for forbidden in ("salary", "gpi", "portability_index", "valuation", "price"):
        assert forbidden not in blob


def test_cli_surfaces_remain_available():
    from apatch.cli import cli

    assert "avatar" in cli.commands
    assert {"episodes", "capabilities", "evidence-export"} <= set(
        cli.commands["avatar"].commands
    )
