from datetime import UTC, datetime
import hashlib
import json

import pytest


def _keypair():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, public, hashlib.sha256(public).hexdigest()[:32]


def _event():
    from avatar_contract import ContributionEvent
    from apatch.contribution import sign_event

    private, public, key_id = _keypair()
    event_id = "e" * 32
    event = ContributionEvent(
        schema_version=2,
        kind="fact",
        event_id=event_id,
        idempotency_key=event_id,
        avatar_id=key_id,
        source="apatch",
        trust_level="attested",
        identity={"key_id": key_id, "subject_type": "human"},
        project={"id": "project-1"},
        session={
            "session_id": "session-1",
            "artifacts": ["spec:SPEC-OUTCOME-1#R1"],
            "started_at": "2026-07-12T10:00:00+00:00",
            "ended_at": "2026-07-12T10:05:00+00:00",
        },
        volume={"ops": 1},
        proof_ref={"op_ids": ["op-1"], "head": "op-1"},
        payload={
            "accepted_taxonomy_refs": ["onet-skill:2.a.2.a"],
            "gate_quality": "falsified",
            "review_submission": {
                "schema_version": 1,
                "objective": "SPEC-OUTCOME-1 - External outcome",
                "delivery_summary": "Delivered the result for customer review.",
                "acceptance_criteria": ["Customer can review the delivered result"],
                "artifact_refs": ["spec:SPEC-OUTCOME-1#R1"],
                "limitations": [],
            },
        },
        created_at="2026-07-12T10:05:00+00:00",
    )
    sign_event(event, type("Provider", (), {"sign": lambda self, data: private.sign(data)})())
    episode_id = hashlib.sha256(
        ("work-episode-v2:" + event_id).encode()
    ).hexdigest()[:40]
    return event.to_wire(), public, key_id, episode_id


def _review_package_id():
    from avatar_contract import build_work_review_package

    return build_work_review_package(
        objective="SPEC-OUTCOME-1 - External outcome",
        delivery_summary="Delivered the result for customer review.",
        acceptance_criteria=["Customer can review the delivered result"],
        source_signature="verified",
        gate="falsified",
        proof_refs=["op-1"],
        artifact_refs=["spec:SPEC-OUTCOME-1#R1"],
    )["review_package_id"]


def _signed_outcome(
    avatar_id: str,
    episode_id: str,
    *,
    organization_id: str = "customer-001",
    verifier_role: str = "client_counterparty",
):
    from avatar_contract import (
        OUTCOME_ATTESTATION_CHAIN_ID,
        OUTCOME_ATTESTATION_EVENT,
        build_outcome_attestation_payload,
    )
    from trustchain import TrustChain, TrustChainConfig

    now = datetime.now(UTC).isoformat()
    payload = build_outcome_attestation_payload(
        request_id="request-001",
        subject_avatar_id=avatar_id,
        work_episode_id=episode_id,
        review_package_id=_review_package_id(),
        task_label="spec:SPEC-OUTCOME-1#R1",
        taxonomy_refs=["onet-skill:2.a.2.a"],
        outcome_status="passed",
        observed_at=now,
        evidence_ref="trustchain://evidence/customer-acceptance-001",
        verifier_organization_id=organization_id,
        verifier_role=verifier_role,
        decision_event_id="decision-001",
        issued_at=now,
    )
    signer = TrustChain(TrustChainConfig(enable_chain=False, enable_nonce=True))
    signed = signer.sign(
        f"{OUTCOME_ATTESTATION_CHAIN_ID}:{OUTCOME_ATTESTATION_EVENT}",
        payload,
        signer_role="tool",
        alg="ed25519",
        bind_custody=True,
    )
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    wire = {
        **payload,
        "trustchain_audit": {
            "chain_id": OUTCOME_ATTESTATION_CHAIN_ID,
            "event": OUTCOME_ATTESTATION_EVENT,
            "algorithm": "ed25519",
            "key_id": signer.get_key_id(),
            "public_key": signer.export_public_key(),
            "signature": signed.signature,
            "signed_at": signed.timestamp,
            "payload_hash": payload_hash,
            "signed_response": signed.to_dict(),
        },
    }
    return wire


def _issuer(outcome: dict) -> dict:
    return {
        "public_key": outcome["trustchain_audit"]["public_key"],
        "organization_id": outcome["verifier"]["organization_id"],
        "roles": [outcome["verifier"]["role"]],
    }


class _Response:
    status_code = 200

    def __init__(self, avatar_id, attestations):
        self.avatar_id = avatar_id
        self.attestations = attestations

    def json(self):
        return {
            "status": "ready",
            "avatar_id": self.avatar_id,
            "attestations": self.attestations,
            "count": len(self.attestations),
        }


class _Client:
    def __init__(self, avatar_id, attestations):
        self.avatar_id = avatar_id
        self.attestations = attestations
        self.calls = []

    def get(self, url, *, params=None, headers):
        self.calls.append((url, params, headers))
        return _Response(self.avatar_id, self.attestations)


class _AvatarPlatformResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


class _AvatarPlatformClient:
    def __init__(self, avatar_id, outcome):
        self.avatar_id = avatar_id
        self.outcome = outcome
        self.uploaded_evidence = []

    def get(self, url, *, headers, params=None):
        if url.endswith("/api/avatar/taxonomy-decisions"):
            return _AvatarPlatformResponse({
                "status": "ready",
                "avatar_id": self.avatar_id,
                "decisions": [],
                "count": 0,
            })
        if url.endswith("/api/avatar/outcomes"):
            return _AvatarPlatformResponse({
                "status": "ready",
                "avatar_id": self.avatar_id,
                "attestations": [self.outcome],
                "count": 1,
            })
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, *, json, headers):
        if url.endswith("/api/avatar/evidence/upload"):
            self.uploaded_evidence.append(json["evidence"])
            return _AvatarPlatformResponse({
                "accepted": True,
                "stored": True,
                "duplicate": False,
                "signature_verified": True,
            })
        raise AssertionError(f"unexpected POST {url}")


def test_pull_pins_counterparty_authority_and_persists_idempotently(tmp_path):
    from apatch.outcome_delivery import pull_outcome_attestations

    _raw, _public, avatar_id, episode_id = _event()
    outcome = _signed_outcome(avatar_id, episode_id)
    client = _Client(avatar_id, [outcome])
    trusted = [_issuer(outcome)]
    first = pull_outcome_attestations(
        base_url="http://tracker:8000",
        avatar_id=avatar_id,
        service_token="secret",
        store_dir=str(tmp_path),
        http_client=client,
        trusted_issuers=trusted,
    )
    second = pull_outcome_attestations(
        base_url="http://tracker:8000",
        avatar_id=avatar_id,
        service_token="secret",
        store_dir=str(tmp_path),
        http_client=client,
        trusted_issuers=trusted,
    )
    assert first["stored"] == 1
    assert second["duplicates"] == 1
    assert client.calls[0][0].endswith("/api/v1/internal/avatar-outcomes")
    assert client.calls[0][2] == {"X-Service-Token": "secret"}


def test_owner_pull_uses_avatar_token_and_pins_returned_subject(tmp_path):
    from apatch.outcome_delivery import pull_outcome_attestations_from_platform

    _raw, _public, avatar_id, episode_id = _event()
    outcome = _signed_outcome(avatar_id, episode_id)
    client = _Client(avatar_id, [outcome])
    trusted = [_issuer(outcome)]

    result = pull_outcome_attestations_from_platform(
        platform_url="https://trust-chain.ai",
        avatar_token="tcav-owner-token",
        avatar_id=avatar_id,
        store_dir=str(tmp_path),
        http_client=client,
        trusted_issuers=trusted,
    )

    assert result["ok"] is True
    assert result["stored"] == 1
    assert client.calls == [
        (
            "https://trust-chain.ai/api/avatar/outcomes",
            None,
            {"Authorization": "Bearer tcav-owner-token"},
        )
    ]


def test_owner_sync_pulls_outcome_recompiles_capability_and_uploads_distillate(
    monkeypatch,
    tmp_path,
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from apatch.avatar_delivery import sync_avatar_state

    raw, public, avatar_id, episode_id = _event()
    outcome = _signed_outcome(avatar_id, episode_id)
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))

    class _Provider:
        def get_public_key(self):
            return public

        def sign(self, data):
            return private.sign(data)

    identity = type("Identity", (), {"key_provider": _Provider()})()
    client = _AvatarPlatformClient(avatar_id, outcome)
    monkeypatch.setattr(
        "apatch.avatar_delivery.tracker_config_from_env",
        lambda: {
            "platform_url": "https://trust-chain.ai",
            "avatar_token": "tcav-owner-token",
            "base_url": "",
            "service_token": "",
        },
    )
    monkeypatch.setattr(
        "apatch.contribution.resolve_identity",
        lambda _target: {"key_id": avatar_id, "trust_level": "attested"},
    )
    monkeypatch.setattr(
        "apatch.trust_identity.load_local_identity",
        lambda _target: identity,
    )
    monkeypatch.setattr("apatch.timesheet.load_events", lambda: [raw])
    monkeypatch.setattr(
        "apatch.episode._local_public_key",
        lambda _root: (avatar_id, public),
    )
    monkeypatch.setattr(
        "apatch.contribution_export.sync_to_trustchain_avatar",
        lambda **_kwargs: {
            "ok": True,
            "accepted": 1,
            "attempted": 1,
            "pending": 0,
            "quarantined": 0,
            "quarantined_total": 0,
            "errors": [],
        },
    )
    monkeypatch.setenv(
        "APATCH_TRUSTED_OUTCOME_ISSUERS",
        json.dumps([_issuer(outcome)]),
    )

    result = sync_avatar_state(
        str(tmp_path),
        evidence_outbox_dir=str(tmp_path / "evidence-outbox"),
        outcome_store_dir=str(tmp_path / "outcomes"),
        taxonomy_store_dir=str(tmp_path / "taxonomy"),
        http_client=client,
    )

    assert result["complete"] is True
    assert result["outcomes"]["stored"] == 1
    assert len(client.uploaded_evidence) == 1
    evidence = client.uploaded_evidence[0]
    assert evidence["episodes"][0]["episode_id"] == episode_id
    assert evidence["episodes"][0]["eligible_for_capability"] is True
    assert evidence["episodes"][0]["outcome"]["basis"] == "external_acceptance"
    assert evidence["capability_estimates"][0]["observations"]["attempted"] == 1
    serialized = json.dumps(evidence).lower()
    assert "salary" not in serialized
    assert "market_price" not in serialized


def test_owner_pull_rejects_another_avatar_before_storage(tmp_path):
    from apatch.outcome_delivery import pull_outcome_attestations_from_platform

    _raw, _public, avatar_id, episode_id = _event()
    outcome = _signed_outcome(avatar_id, episode_id)
    client = _Client("another-avatar", [outcome])

    result = pull_outcome_attestations_from_platform(
        platform_url="https://trust-chain.ai",
        avatar_token="tcav-owner-token",
        avatar_id=avatar_id,
        store_dir=str(tmp_path),
        http_client=client,
        trusted_issuers=[_issuer(outcome)],
    )

    assert result["ok"] is False
    assert result["status"] == "platform_rejected"
    assert list(tmp_path.rglob("*.json")) == []


def test_signed_external_outcome_replaces_only_the_matching_proxy(
    monkeypatch, tmp_path
):
    from apatch.episode import episode_from_event
    from apatch.outcome_delivery import load_outcome_index, store_outcome_attestation

    raw, public, avatar_id, episode_id = _event()
    outcome = _signed_outcome(avatar_id, episode_id)
    monkeypatch.setenv("APATCH_AVATAR_OUTCOME_STORE", str(tmp_path))
    monkeypatch.setenv(
        "APATCH_TRUSTED_OUTCOME_ISSUERS",
        json.dumps([_issuer(outcome)]),
    )
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (avatar_id, public))
    store_outcome_attestation(outcome, store_dir=str(tmp_path))

    episode = episode_from_event(
        raw,
        str(tmp_path),
        outcome_index=load_outcome_index(avatar_id=avatar_id),
    )
    assert episode["episode_id"] == episode_id
    assert episode["outcome"]["basis"] == "external_acceptance"
    assert episode["outcome"]["reference"].startswith("outcome-attestation:")
    assert episode["evidence_quality"]["outcome"] == "observed"


def test_recompile_reads_the_explicit_outcome_store_without_global_env(
    monkeypatch,
    tmp_path,
):
    from apatch.episode import build_episodes
    from apatch.outcome_delivery import store_outcome_attestation

    raw, public, avatar_id, episode_id = _event()
    outcome = _signed_outcome(avatar_id, episode_id)
    outcome_store = tmp_path / "outcomes"
    monkeypatch.delenv("APATCH_AVATAR_OUTCOME_STORE", raising=False)
    monkeypatch.setenv(
        "APATCH_TRUSTED_OUTCOME_ISSUERS",
        json.dumps([_issuer(outcome)]),
    )
    monkeypatch.setattr("apatch.timesheet.load_events", lambda: [raw])
    monkeypatch.setattr(
        "apatch.episode._local_public_key",
        lambda _root: (avatar_id, public),
    )
    store_outcome_attestation(outcome, store_dir=str(outcome_store))

    episodes = build_episodes(
        str(tmp_path),
        avatar_id=avatar_id,
        outcome_store_dir=str(outcome_store),
    )

    assert len(episodes) == 1
    assert episodes[0]["episode_id"] == episode_id
    assert episodes[0]["outcome"]["basis"] == "external_acceptance"
    assert episodes[0]["eligible_for_capability"] is True


def test_untrusted_outcome_never_enters_episode_index(monkeypatch, tmp_path):
    from apatch.outcome_delivery import load_outcome_index

    _raw, _public, avatar_id, episode_id = _event()
    outcome = _signed_outcome(avatar_id, episode_id)
    path = tmp_path / avatar_id / f"{outcome['attestation_id']}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(outcome), encoding="utf-8")
    monkeypatch.setenv("APATCH_AVATAR_OUTCOME_STORE", str(tmp_path))
    issuer = _issuer(outcome)
    issuer["public_key"] = "not-the-counterparty-key"
    monkeypatch.setenv("APATCH_TRUSTED_OUTCOME_ISSUERS", json.dumps([issuer]))

    assert load_outcome_index(avatar_id=avatar_id) == {}


def test_association_signed_outcome_never_enters_capability_index(monkeypatch, tmp_path):
    from apatch.outcome_delivery import load_outcome_index

    _raw, _public, avatar_id, episode_id = _event()
    outcome = _signed_outcome(
        avatar_id,
        episode_id,
        organization_id="association-001",
        verifier_role="independent_association",
    )
    path = tmp_path / avatar_id / f"{outcome['attestation_id']}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(outcome), encoding="utf-8")
    monkeypatch.setenv(
        "APATCH_TRUSTED_OUTCOME_ISSUERS",
        json.dumps([{
            "public_key": outcome["trustchain_audit"]["public_key"],
            "organization_id": "association-001",
            "roles": ["professional_association"],
        }]),
    )

    assert load_outcome_index(avatar_id=avatar_id) == {}
