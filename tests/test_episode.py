"""WorkEpisode v2: signed receipt, outcome and attribution gates."""
import copy
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


def _event(
    *,
    subject_type="human",
    outcome=True,
    signature=True,
    review_submission=True,
):
    from avatar_contract import ContributionEvent

    private, public, key_id = _keypair()
    payload = {
        "accepted_taxonomy_refs": ["soc:15-1252", "onet-task:1234"],
        "gate_quality": "falsified",
    }
    if review_submission:
        payload["review_submission"] = {
            "schema_version": 1,
            "objective": "SPEC-AVATAR-1 - Avatar evidence",
            "delivery_summary": "Delivered the governed Avatar evidence flow.",
            "acceptance_criteria": ["The signed evidence flow is reviewable"],
            "artifact_refs": ["spec:SPEC-AVATAR-1#R1"],
            "limitations": [],
        }
    if outcome:
        payload["outcome"] = {
            "status": "accepted",
            "basis": "external_acceptance",
            "observed_at": "2026-07-12T10:05:00+00:00",
            "reference": "ticket:done-1",
        }
    event = ContributionEvent(
        schema_version=2,
        kind="fact",
        event_id="e" * 32,
        idempotency_key="e" * 32,
        avatar_id=key_id,
        source="apatch",
        trust_level="attested",
        identity={"key_id": key_id, "subject_type": subject_type},
        project={"id": "project-1", "name": "demo"},
        session={
            "session_id": "session-1",
            "intent": "private task title",
            "artifacts": ["spec:SPEC-AVATAR-1#R1"],
            "started_at": "2026-07-12T10:00:00+00:00",
            "ended_at": "2026-07-12T10:05:00+00:00",
        },
        volume={"ops": 2, "files_touched": 1, "insertions": 10, "deletions": 1},
        proof_ref={"op_ids": ["op-1", "op-2"], "head": "op-2"},
        payload=payload,
        created_at="2026-07-12T10:05:00+00:00",
    )
    event.validate()
    if signature:
        from apatch.contribution import sign_event

        sign_event(event, type("Provider", (), {"sign": lambda self, data: private.sign(data)})())
    return event.to_wire(), public, key_id


def _outcome_index(episode, *, review_package_id=None):
    return {
        episode["episode_id"]: {
            "attestation_id": "a" * 40,
            "subject_avatar_id": episode["avatar_id"],
            "work_episode_id": episode["episode_id"],
            "review_package_id": (
                review_package_id
                or episode["review_package"]["review_package_id"]
            ),
            "task": {
                "label": episode["task"]["label"],
                "taxonomy_refs": episode["task"]["taxonomy_refs"],
            },
            "outcome": {
                "status": "passed",
                "observed_at": "2026-07-12T10:06:00+00:00",
            },
        }
    }


def test_signed_observed_human_episode_is_eligible(monkeypatch, tmp_path):
    raw, public, key_id = _event()
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    from apatch.episode import episode_from_event

    pending = episode_from_event(raw, str(tmp_path))
    episode = episode_from_event(
        raw,
        str(tmp_path),
        outcome_index=_outcome_index(pending),
    )
    assert episode["eligible_for_capability"] is True
    assert episode["exclusion_reasons"] == []
    assert episode["avatar_id"] == key_id
    assert episode["attribution"]["mode"] == "direct_human"
    assert episode["outcome"]["status"] == "passed"
    assert episode["evidence_quality"] == {
        "signature": "verified",
        "gate": "falsified",
        "outcome": "observed",
        "rights": "confirmed",
    }
    assert episode["task"]["label"] == "spec:SPEC-AVATAR-1#R1"


def test_signed_review_submission_becomes_ready_counterparty_package(
    monkeypatch, tmp_path
):
    raw, public, key_id = _event()
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    from apatch.episode import episode_from_event

    episode = episode_from_event(raw, str(tmp_path))
    package = episode["review_package"]
    assert package["status"] == "ready"
    assert package["objective"] == "SPEC-AVATAR-1 - Avatar evidence"
    assert package["delivery_summary"] == "Delivered the governed Avatar evidence flow."
    assert package["verification"] == {
        "source_signature": "verified",
        "gate": "falsified",
        "proof_refs": ["op-1", "op-2"],
    }
    assert "private task title" not in json.dumps(package)



def test_hashed_spec_reference_stays_exact_across_episode_and_review_package(
    monkeypatch, tmp_path
):
    raw, _public, _key_id = _event()
    exact_ref = "spec:SPEC-AVATAR-1#R1@sha256:1234abcd"
    raw["session"]["artifacts"] = [{
        "kind": "spec",
        "id": "SPEC-AVATAR-1#R1",
        "content_hash": "sha256:1234abcd",
    }]
    raw["payload"]["review_submission"]["artifact_refs"] = [exact_ref]
    monkeypatch.setattr(
        "apatch.episode._signature_status", lambda _raw, _root: "verified"
    )
    from apatch.episode import episode_from_event

    episode = episode_from_event(raw, str(tmp_path))

    assert episode["task"]["spec_refs"] == [exact_ref]
    assert episode["review_package"]["artifact_refs"] == [exact_ref]

def test_private_intent_is_never_guessed_into_legacy_review_package(
    monkeypatch, tmp_path
):
    raw, public, key_id = _event(review_submission=False)
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    from apatch.episode import episode_from_event

    package = episode_from_event(raw, str(tmp_path))["review_package"]
    assert package["status"] == "incomplete"
    assert package["objective"] is None
    assert package["delivery_summary"] is None
    assert "private task title" not in json.dumps(package)


def test_producer_self_claim_never_counts_as_external_acceptance(
    monkeypatch, tmp_path
):
    raw, public, key_id = _event(outcome=True)
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    from apatch.episode import episode_from_event

    episode = episode_from_event(raw, str(tmp_path))
    assert episode["outcome"]["basis"] == "technical_gate"
    assert episode["evidence_quality"]["outcome"] == "proxy"
    assert episode["eligible_for_capability"] is False


def test_outcome_attestation_must_match_exact_ready_review_package(
    monkeypatch, tmp_path
):
    raw, public, key_id = _event()
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    from apatch.episode import episode_from_event

    pending = episode_from_event(raw, str(tmp_path))
    mismatch = episode_from_event(
        raw,
        str(tmp_path),
        outcome_index=_outcome_index(pending, review_package_id="0" * 40),
    )
    assert mismatch["evidence_quality"]["outcome"] == "proxy"

    accepted = episode_from_event(
        raw,
        str(tmp_path),
        outcome_index=_outcome_index(pending),
    )
    assert accepted["evidence_quality"]["outcome"] == "observed"
    assert accepted["eligible_for_capability"] is True


def test_invalid_signature_and_unknown_role_fail_closed(monkeypatch, tmp_path):
    raw, public, key_id = _event(subject_type="")
    raw["signature"] = "AAAA"
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    from apatch.episode import episode_from_event

    episode = episode_from_event(raw, str(tmp_path))
    assert episode["eligible_for_capability"] is False
    assert "signature_not_verified" in episode["exclusion_reasons"]
    assert "attribution_unknown" in episode["exclusion_reasons"]


def test_attestation_only_is_a_proxy_not_external_outcome(monkeypatch, tmp_path):
    raw, public, key_id = _event(outcome=False)
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    from apatch.episode import episode_from_event

    episode = episode_from_event(raw, str(tmp_path))
    assert episode["eligible_for_capability"] is False
    assert episode["outcome"]["basis"] == "technical_gate"
    assert episode["evidence_quality"]["outcome"] == "proxy"
    assert "outcome_not_independently_observed" in episode["exclusion_reasons"]


def test_bare_ledger_rows_can_never_create_an_episode():
    from apatch.episode import episodes_from_rows

    rows = [{"id": "unsigned-op", "payload": {"session_id": "s"}}]
    assert episodes_from_rows(rows, {"key_id": "a" * 32}, {"id": "p"}) == []


def test_missing_legacy_timestamp_is_deterministic_not_wall_clock(
    monkeypatch, tmp_path
):
    raw, _public, _key_id = _event(outcome=False)
    raw.pop("created_at", None)
    raw["session"].pop("started_at", None)
    raw["session"].pop("ended_at", None)
    monkeypatch.setattr(
        "apatch.episode._signature_status", lambda _raw, _root: "verified"
    )
    from apatch.episode import episode_from_event

    first = episode_from_event(raw, str(tmp_path))
    second = episode_from_event(raw, str(tmp_path))

    assert first == second
    assert first["occurred_at"] == "1970-01-01T00:00:00+00:00"
    assert first["outcome"]["observed_at"] == "1970-01-01T00:00:00+00:00"


def test_full_episode_rederivation_detects_any_field_tamper(monkeypatch, tmp_path):
    raw, public, key_id = _event()
    store = tmp_path / "store" / key_id
    store.mkdir(parents=True)
    (store / "event.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("APATCH_CONTRIB_STORE", str(tmp_path / "store"))
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    monkeypatch.setattr(
        "apatch.contribution.resolve_identity",
        lambda _root: {"key_id": key_id, "trust_level": "attested"},
    )
    from apatch.episode import build_episodes, verify_episodes

    episodes = build_episodes(str(tmp_path))
    assert verify_episodes(episodes, str(tmp_path))["ok"] is True
    tampered = copy.deepcopy(episodes)
    tampered[0]["task"]["label"] = "forged"
    check = verify_episodes(tampered, str(tmp_path))
    assert check["ok"] is False
    assert check["drift"][0]["error"] == "episode_drift"


def test_volume_does_not_exist_on_the_episode_signal_surface(monkeypatch, tmp_path):
    raw, public, key_id = _event()
    monkeypatch.setattr("apatch.episode._local_public_key", lambda _root: (key_id, public))
    from apatch.episode import episode_from_event

    baseline = episode_from_event(raw, str(tmp_path))
    raw["volume"] = {"ops": 10_000_000, "files_touched": 10_000_000}
    # Signature now fails, rather than volume silently buying evidence.
    changed = episode_from_event(raw, str(tmp_path))
    assert "volume" not in baseline
    assert changed["evidence_quality"]["signature"] == "invalid"
