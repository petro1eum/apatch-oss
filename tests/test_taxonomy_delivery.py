from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest


@pytest.fixture(autouse=True)
def _isolate_host_avatar_trust_policy(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "APATCH_AVATAR_TRUST_POLICY",
        str(tmp_path / "missing-avatar-trust-policy.json"),
    )


def _event(*, kind: str = "fact") -> tuple[dict, str, str]:
    from avatar_contract import ContributionEvent

    avatar_id = "a" * 32
    event_id = "e" * 32 if kind == "fact" else "c" * 32
    event = ContributionEvent(
        schema_version=3,
        kind=kind,
        event_id=event_id,
        idempotency_key=event_id,
        avatar_id=avatar_id,
        source="apatch",
        trust_level="attested",
        identity={"key_id": avatar_id, "subject_type": "human"},
        project={"id": "project-1"},
        session={
            "session_id": "session-1",
            "intent": "Design and test a software database application",
            "artifacts": [{"kind": "bug", "id": "BUG-1"}],
            "started_at": "2026-07-18T10:00:00+00:00",
            "ended_at": "2026-07-18T10:05:00+00:00",
        },
        volume={"ops": 2},
        proof_ref={"op_ids": ["op-1"], "head": "op-1"},
        payload={"gate_quality": "falsified"} if kind == "fact" else None,
        cv_delta={"quality": 0.1} if kind == "claim" else None,
        declares_for="e" * 32 if kind == "claim" else None,
        created_at="2026-07-18T10:05:00+00:00",
        signature="signed-event",
    )
    episode_id = hashlib.sha256(
        ("work-episode-v2:" + event_id).encode("utf-8")
    ).hexdigest()[:40]
    return event.to_wire(), avatar_id, episode_id


def _signed_decision(
    avatar_id: str,
    episode_id: str,
    *,
    status: str = "accepted",
    supersedes: str | None = None,
    issued_at: datetime | None = None,
    signer=None,
) -> tuple[dict, object]:
    from avatar_contract import (
        TAXONOMY_DECISION_CHAIN_ID,
        TAXONOMY_DECISION_EVENT,
        build_taxonomy_decision_payload,
    )
    from trustchain import TrustChain, TrustChainConfig

    event_id = "e" * 32
    signer = signer or TrustChain(TrustChainConfig(enable_chain=False, enable_nonce=True))
    payload = build_taxonomy_decision_payload(
        subject_avatar_id=avatar_id,
        source_event_id=event_id,
        work_episode_id=episode_id,
        proposal_id=hashlib.sha256(b"proposal").hexdigest()[:40],
        catalog_release="29.3",
        matcher_name="hc-onet-owner-review",
        matcher_version="1.0.0",
        intent_sha256=hashlib.sha256(
            b"Design and test a software database application"
        ).hexdigest(),
        occupation={"soc_code": "15-1252.00", "title": "Software Developers"},
        task={"id": "10001", "statement": "Design and test software applications."},
        skills=[{"id": "2.A.2.a", "name": "Critical Thinking"}],
        taxonomy_refs=(
            ["soc:15-1252", "onet-task:10001", "onet-skill:2.A.2.a"]
            if status == "accepted"
            else []
        ),
        status=status,
        reason_code="owner_confirmed" if status == "accepted" else "owner_rejected",
        supersedes_decision_id=supersedes,
        owner_professional_id="11111111-1111-4111-8111-111111111111",
        owner_decision_event_id=f"owner-{status}-1",
        issued_at=(issued_at or datetime.now(UTC)).isoformat(),
    )
    signed = signer.sign(
        f"{TAXONOMY_DECISION_CHAIN_ID}:{TAXONOMY_DECISION_EVENT}",
        payload,
        signer_role="tool",
        alg="ed25519",
        bind_custody=True,
    )
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ({
        **payload,
        "trustchain_audit": {
            "chain_id": TAXONOMY_DECISION_CHAIN_ID,
            "event": TAXONOMY_DECISION_EVENT,
            "algorithm": "ed25519",
            "key_id": signer.get_key_id(),
            "public_key": signer.export_public_key(),
            "signature": signed.signature,
            "signed_at": signed.timestamp,
            "payload_hash": payload_hash,
            "signed_response": signed.to_dict(),
        },
    }, signer)


class _Response:
    status_code = 200

    def __init__(self, avatar_id: str, decisions: list[dict]):
        self.avatar_id = avatar_id
        self.decisions = decisions

    def json(self):
        return {
            "status": "ready",
            "avatar_id": self.avatar_id,
            "decisions": self.decisions,
            "count": len(self.decisions),
        }


class _Client:
    def __init__(self, avatar_id: str, decisions: list[dict]):
        self.avatar_id = avatar_id
        self.decisions = decisions
        self.calls = []

    def get(self, url, *, params=None, headers):
        self.calls.append((url, params, headers))
        return _Response(self.avatar_id, self.decisions)


def test_pull_pins_tracker_key_and_persists_idempotently(tmp_path):
    from apatch.taxonomy_delivery import pull_taxonomy_decisions

    _raw, avatar_id, episode_id = _event()
    decision, _signer = _signed_decision(avatar_id, episode_id)
    client = _Client(avatar_id, [decision])
    trusted = [decision["trustchain_audit"]["public_key"]]
    first = pull_taxonomy_decisions(
        base_url="http://tracker:8000",
        avatar_id=avatar_id,
        service_token="secret",
        store_dir=str(tmp_path),
        http_client=client,
        trusted_public_keys=trusted,
    )
    second = pull_taxonomy_decisions(
        base_url="http://tracker:8000",
        avatar_id=avatar_id,
        service_token="secret",
        store_dir=str(tmp_path),
        http_client=client,
        trusted_public_keys=trusted,
    )
    assert first["stored"] == 1
    assert second["duplicates"] == 1
    assert client.calls[0][0].endswith("/api/v1/internal/avatar-taxonomy-decisions")
    assert client.calls[0][1] == {"avatar_id": avatar_id, "limit": 1000}


def test_latest_superseding_rejection_removes_acceptance(tmp_path):
    from apatch.taxonomy_delivery import load_taxonomy_index, store_taxonomy_decision

    _raw, avatar_id, episode_id = _event()
    now = datetime.now(UTC)
    accepted, signer = _signed_decision(
        avatar_id,
        episode_id,
        issued_at=now,
    )
    rejected, _ = _signed_decision(
        avatar_id,
        episode_id,
        status="rejected",
        supersedes=accepted["decision_id"],
        issued_at=now + timedelta(seconds=1),
        signer=signer,
    )
    trusted = [accepted["trustchain_audit"]["public_key"]]
    store_taxonomy_decision(accepted, store_dir=str(tmp_path), trusted_public_keys=trusted)
    store_taxonomy_decision(rejected, store_dir=str(tmp_path), trusted_public_keys=trusted)
    latest = load_taxonomy_index(avatar_id=avatar_id, store_dir=str(tmp_path))
    assert latest["e" * 32]["decision"]["status"] == "rejected"


def test_accepted_decision_classifies_episode_but_proposal_alone_does_not(
    monkeypatch,
    tmp_path,
):
    from apatch.episode import episode_from_event

    raw, avatar_id, episode_id = _event()
    decision, _ = _signed_decision(avatar_id, episode_id)
    monkeypatch.setattr("apatch.episode._signature_status", lambda *_args: "verified")

    baseline = episode_from_event(raw, str(tmp_path))
    classified = episode_from_event(
        raw,
        str(tmp_path),
        taxonomy_index={raw["event_id"]: decision},
    )
    assert "taxonomy_missing" in baseline["exclusion_reasons"]
    assert classified["task"]["label"] == "Design and test software applications."
    assert classified["task"]["taxonomy_refs"] == [
        "onet-skill:2.A.2.a",
        "onet-task:10001",
        "soc:15-1252",
    ]
    assert "taxonomy_missing" not in classified["exclusion_reasons"]
    assert "outcome_not_independently_observed" in classified["exclusion_reasons"]


def test_owner_classification_does_not_invalidate_prior_counterparty_outcome(
    monkeypatch,
    tmp_path,
):
    from apatch.episode import episode_from_event

    raw, avatar_id, episode_id = _event()
    raw["payload"]["review_submission"] = {
        "schema_version": 1,
        "objective": "Design and test a software database application",
        "delivery_summary": "Delivered a reviewable software data model.",
        "acceptance_criteria": ["The data model represents the required records"],
        "artifact_refs": ["bug:BUG-1"],
        "limitations": [],
    }
    decision, _ = _signed_decision(avatar_id, episode_id)
    monkeypatch.setattr("apatch.episode._signature_status", lambda *_args: "verified")
    source_episode = episode_from_event(raw, str(tmp_path))
    outcome_index = {
        episode_id: {
            "attestation_id": "a" * 40,
            "subject_avatar_id": avatar_id,
            "work_episode_id": episode_id,
            "review_package_id": source_episode["review_package"]["review_package_id"],
            "task": source_episode["task"],
            "outcome": {
                "status": "passed",
                "observed_at": "2026-07-18T10:06:00+00:00",
            },
        }
    }

    classified = episode_from_event(
        raw,
        str(tmp_path),
        outcome_index=outcome_index,
        taxonomy_index={raw["event_id"]: decision},
    )

    assert classified["task"]["taxonomy_refs"] == [
        "onet-skill:2.A.2.a",
        "onet-task:10001",
        "soc:15-1252",
    ]
    assert classified["outcome"]["basis"] == "external_acceptance"
    assert classified["evidence_quality"]["outcome"] == "observed"
    assert classified["eligible_for_capability"] is True


def test_wrong_intent_hash_cannot_relabel_episode(monkeypatch, tmp_path):
    from apatch.episode import episode_from_event

    raw, avatar_id, episode_id = _event()
    decision, _ = _signed_decision(avatar_id, episode_id)
    raw["session"]["intent"] = "another task"
    monkeypatch.setattr("apatch.episode._signature_status", lambda *_args: "verified")
    episode = episode_from_event(
        raw,
        str(tmp_path),
        taxonomy_index={raw["event_id"]: decision},
    )
    assert episode["task"]["taxonomy_refs"] == []
    assert "taxonomy_missing" in episode["exclusion_reasons"]


def test_claim_events_never_become_work_episodes(monkeypatch):
    import apatch.outcome_delivery as outcomes
    import apatch.taxonomy_delivery as taxonomy
    from apatch.episode import episodes_from_events

    raw, avatar_id, _episode_id_value = _event(kind="claim")
    monkeypatch.setattr(outcomes, "load_outcome_index", lambda **_kwargs: {})
    monkeypatch.setattr(taxonomy, "load_taxonomy_index", lambda **_kwargs: {})
    assert episodes_from_events([raw], avatar_id=avatar_id) == []


def test_production_requires_pinned_tracker_key(monkeypatch):
    from apatch.taxonomy_delivery import TaxonomyDecisionError, validate_taxonomy_decision

    _raw, avatar_id, episode_id = _event()
    decision, _ = _signed_decision(avatar_id, episode_id)
    monkeypatch.setenv("TC_ENVIRONMENT", "production")
    monkeypatch.delenv("APATCH_TRUSTED_TAXONOMY_PUBLIC_KEYS", raising=False)
    with pytest.raises(TaxonomyDecisionError, match="requires"):
        validate_taxonomy_decision(decision)


def test_production_pull_fails_before_transport_when_issuer_pin_is_missing(
    monkeypatch,
    tmp_path,
):
    from apatch.taxonomy_delivery import pull_taxonomy_decisions

    _raw, avatar_id, _episode_id_value = _event()
    client = _Client(avatar_id, [])
    monkeypatch.setenv("TC_ENVIRONMENT", "production")
    monkeypatch.delenv("APATCH_TRUSTED_TAXONOMY_PUBLIC_KEYS", raising=False)
    result = pull_taxonomy_decisions(
        base_url="http://tracker:8000",
        avatar_id=avatar_id,
        store_dir=str(tmp_path),
        http_client=client,
    )
    assert result["ok"] is False
    assert result["status"] == "issuer_unconfigured"
    assert client.calls == []
