from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import governed_work as G
from apatch import governed_work_delivery as D
from apatch import governed_work_transport as T


class Provider:
    def __init__(self):
        self.private = Ed25519PrivateKey.generate()

    def get_public_key(self):
        return self.private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def sign(self, payload):
        return self.private.sign(payload)


def platform_sign(body, platform: Provider, purpose: str):
    payload = (
        b"TrustChain-Governed-Work\x00v1\x00"
        + purpose.encode("ascii")
        + b"\x00"
        + G.canonical_bytes(body)
    )
    return {
        **body,
        "signature": {
            "algorithm": "Ed25519",
            "key_id": G.signer_key_id(platform),
            "value": G._b64url_encode(platform.sign(payload)),
        },
    }


class Response:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code

    def json(self):
        return self.body


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, content, headers):
        self.calls.append({"url": url, "content": content, "headers": headers})
        return self.responses.pop(0)


def make_change(actor: Provider):
    body = {
        "schema": G.CHANGE_SCHEMA,
        "change_id": "apchg_" + "1" * 32,
        "execution_system": "apatch",
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "2" * 32,
        "source_kind": "work_program",
        "work_program_id": "tcwp_" + "3" * 32,
        "work_program_hash": "sha256:" + "4" * 64,
        "context_release_id": None,
        "context_release_manifest_hash": None,
        "spec_id": "SPEC-WORK-1",
        "spec_hash": "sha256:" + "5" * 64,
        "requirement_refs": [
            {"requirement_id": "R1", "requirement_hash": "sha256:" + "6" * 16}
        ],
        "purpose_hash": "sha256:" + "7" * 64,
        "actor_key_id": G.signer_key_id(actor),
        "issued_at": "2026-08-30T00:00:00Z",
    }
    return G.sign_document(body, actor, key_id=G.signer_key_id(actor))


def make_binding(change, platform: Provider):
    body = {
        "schema": G.SOURCE_BINDING_SCHEMA,
        "binding_id": "tcpsb_" + "8" * 32,
        "tenant_id": change["tenant_id"],
        "project_group_id": change["project_group_id"],
        "source_kind": "work_program",
        "work_program_id": change["work_program_id"],
        "work_program_hash": change["work_program_hash"],
        "context_release_id": None,
        "context_release_manifest_hash": None,
        "execution_system": "apatch",
        "change_id": change["change_id"],
        "change_hash": G.document_hash(change),
        "spec_id": change["spec_id"],
        "spec_hash": change["spec_hash"],
        "requirement_refs": change["requirement_refs"],
        "actor_ref": "agent_binding:tcpgab_" + "9" * 32,
        "authority_version": 1,
        "issued_at": "2026-08-30T00:01:00Z",
    }
    return platform_sign(
        body,
        platform,
        G.PLATFORM_SOURCE_BINDING_PURPOSE,
    )


def config(platform: Provider, actor: Provider):
    return {
        "schema": D.CONFIG_SCHEMA,
        "platform_url": "https://platform.example",
        "client_id": "apatch:test",
        "service_request_key_id": T.service_request_key_id(actor),
        "binding_authority_keys": {
            G.signer_key_id(platform): G._b64url_encode(platform.get_public_key())
        },
    }


def make_evidence(actor: Provider, binding):
    timesheet_body = {
        "schema": G.TIMESHEET_SCHEMA,
        "timesheet_id": "apts_" + "a" * 32,
        "project_source_binding_id": binding["binding_id"],
        "project_source_binding_hash": G.document_hash(binding),
        "subject_key_id": G.signer_key_id(actor),
        "period_started_at": "2026-08-30T00:00:00Z",
        "period_ended_at": "2026-08-30T00:10:00Z",
        "claimed_active_seconds": 600,
        "session_refs": [
            {
                "governed_session_id": "session-1",
                "started_at": "2026-08-30T00:00:00Z",
                "ended_at": "2026-08-30T00:10:00Z",
            }
        ],
        "contribution_event_refs": [
            {"event_id": "event-1", "event_hash": "sha256:" + "b" * 64}
        ],
        "issued_at": "2026-08-30T00:11:00Z",
    }
    timesheet = G.sign_document(
        timesheet_body, actor, key_id=G.signer_key_id(actor)
    )
    evidence_body = {
        "schema": G.EVIDENCE_SCHEMA,
        "bundle_id": "apweb_" + "c" * 32,
        "change_id": binding["change_id"],
        "change_hash": binding["change_hash"],
        "project_source_binding_id": binding["binding_id"],
        "project_source_binding_hash": G.document_hash(binding),
        "spec_id": binding["spec_id"],
        "spec_hash": binding["spec_hash"],
        "requirement_refs": binding["requirement_refs"],
        "attestation_refs": [
            {
                "spec_id": binding["spec_id"],
                "requirement_id": "R1",
                "attestation_id": "att-1",
                "attestation_hash": "sha256:" + "d" * 64,
                "outcome": "passed",
            }
        ],
        "contribution_event_refs": timesheet["contribution_event_refs"],
        "timesheet_ref": {
            "timesheet_id": timesheet["timesheet_id"],
            "timesheet_hash": G.document_hash(timesheet),
            "claimed_active_seconds": 600,
        },
        "issued_at": "2026-08-30T00:12:00Z",
    }
    evidence = G.sign_document(
        evidence_body, actor, key_id=G.signer_key_id(actor)
    )
    return evidence, timesheet


def make_receipt(entry, evidence, platform: Provider):
    body = {
        "schema": D.RECEIPT_SCHEMA,
        "receipt_id": "tcgwar_" + "e" * 32,
        "tenant_id": entry["tenant_id"],
        "project_group_id": entry["project_group_id"],
        "request_hash": entry["request_hash"],
        "command": "evidence_admission",
        "resource_id": evidence["bundle_id"],
        "resource_hash": G.document_hash(evidence),
        "projection_cursor": 7,
        "accepted_at": "2026-08-30T00:13:00Z",
    }
    return platform_sign(
        body,
        platform,
        G.PLATFORM_EVIDENCE_ADMISSION_PURPOSE,
    )




def test_platform_url_transition_is_signed_cas_and_idempotent(tmp_path):
    actor, platform = Provider(), Provider()
    initial = config(platform, actor)
    initial["platform_url"] = "https://keys.trust-chain.ai"
    D.write_config(str(tmp_path), initial)

    first = D.transition_platform_url(
        str(tmp_path),
        expected_platform_url="https://keys.trust-chain.ai",
        platform_url="https://clients.trust-chain.ai/",
        idempotency_key="prod-surface-transition-20260831",
        changed_at="2026-08-31T00:00:00Z",
        key_provider=actor,
    )
    assert first["ok"] is True
    assert first["stored"] is True
    assert first["recovered"] is False
    assert first["platform_url"] == "https://clients.trust-chain.ai"

    current = D.load_config(str(tmp_path))
    expected = dict(initial)
    expected["platform_url"] = "https://clients.trust-chain.ai"
    assert current == expected

    transition_path = next(
        (tmp_path / ".apatch/governed_work/config_transitions").glob("*.json")
    )
    transition = json.loads(transition_path.read_text())
    assert transition["previous_platform_url"] == "https://keys.trust-chain.ai"
    assert transition["platform_url"] == "https://clients.trust-chain.ai"
    assert "prod-surface-transition-20260831" not in transition_path.read_text()
    assert G.verify_document_signature(
        transition,
        {G.signer_key_id(actor): actor.get_public_key()},
    ) == G.signer_key_id(actor)

    replay = D.transition_platform_url(
        str(tmp_path),
        expected_platform_url="https://keys.trust-chain.ai",
        platform_url="https://clients.trust-chain.ai",
        idempotency_key="prod-surface-transition-20260831",
        key_provider=actor,
    )
    assert replay["stored"] is False
    assert replay["recovered"] is False
    assert replay["transition_id"] == first["transition_id"]
    assert replay["transition_hash"] == first["transition_hash"]

    with pytest.raises(G.GovernedWorkError, match="idempotency conflict: platform_url"):
        D.transition_platform_url(
            str(tmp_path),
            expected_platform_url="https://keys.trust-chain.ai",
            platform_url="https://other-client.example",
            idempotency_key="prod-surface-transition-20260831",
            key_provider=actor,
        )
    assert D.load_config(str(tmp_path))["platform_url"] == (
        "https://clients.trust-chain.ai"
    )

    with pytest.raises(G.GovernedWorkError, match="expected_platform_url"):
        D.transition_platform_url(
            str(tmp_path),
            expected_platform_url="https://wrong.example",
            platform_url="https://another.example",
            idempotency_key="different-transition-20260831",
            key_provider=actor,
        )


def test_platform_url_transition_recovers_after_config_write_crash(
    tmp_path,
    monkeypatch,
):
    actor, platform = Provider(), Provider()
    D.write_config(str(tmp_path), config(platform, actor))
    real_atomic_json = D._atomic_json
    failed = {"once": False}

    def crash_after_intent(path, document):
        if (
            path == D.config_path(str(tmp_path))
            and document.get("platform_url") == "https://clients.trust-chain.ai"
            and not failed["once"]
        ):
            failed["once"] = True
            raise OSError("simulated config replace crash")
        return real_atomic_json(path, document)

    monkeypatch.setattr(D, "_atomic_json", crash_after_intent)
    with pytest.raises(OSError, match="simulated config replace crash"):
        D.transition_platform_url(
            str(tmp_path),
            expected_platform_url="https://platform.example",
            platform_url="https://clients.trust-chain.ai",
            idempotency_key="crash-recovery-transition-20260831",
            changed_at="2026-08-31T00:00:00Z",
            key_provider=actor,
        )
    assert D.load_config(str(tmp_path))["platform_url"] == (
        "https://platform.example"
    )

    monkeypatch.setattr(D, "_atomic_json", real_atomic_json)
    recovered = D.transition_platform_url(
        str(tmp_path),
        expected_platform_url="https://platform.example",
        platform_url="https://clients.trust-chain.ai",
        idempotency_key="crash-recovery-transition-20260831",
        key_provider=actor,
    )
    assert recovered["stored"] is False
    assert recovered["recovered"] is True
    assert D.load_config(str(tmp_path))["platform_url"] == (
        "https://clients.trust-chain.ai"
    )


def test_outbox_retirement_requires_acknowledged_same_scope_replacement(
    tmp_path,
):
    actor, platform = Provider(), Provider()
    change = make_change(actor)
    binding = make_binding(change, platform)
    evidence, timesheet = make_evidence(actor, binding)
    first = D.queue_evidence_admission(
        str(tmp_path),
        evidence_bundle=evidence,
        timesheet_draft=timesheet,
        tenant_id=change["tenant_id"],
        project_group_id=change["project_group_id"],
        client_id="apatch:test",
    )

    replacement_body = {
        key: value for key, value in evidence.items() if key != "signature"
    }
    replacement_body["bundle_id"] = "apweb_" + "f" * 32
    replacement = G.sign_document(
        replacement_body,
        actor,
        key_id=G.signer_key_id(actor),
    )
    second = D.queue_evidence_admission(
        str(tmp_path),
        evidence_bundle=replacement,
        timesheet_draft=timesheet,
        tenant_id=change["tenant_id"],
        project_group_id=change["project_group_id"],
        client_id="apatch:test",
    )
    assert D.pending_count(str(tmp_path)) == 2

    with pytest.raises(
        G.GovernedWorkError,
        match="replacement outbox entry is not acknowledged",
    ):
        D.retire_outbox_entry(
            str(tmp_path),
            entry_id=first["entry_id"],
            superseded_by_entry_id=second["entry_id"],
            key_provider=actor,
        )

    replacement_path, replacement_entry = D._find_outbox_entry(
        str(tmp_path),
        second["entry_id"],
    )
    G._atomic_json(
        D._ack_path(replacement_path),
        {
            "entry_id": replacement_entry["entry_id"],
            "request_hash": replacement_entry["request_hash"],
        },
    )
    retired = D.retire_outbox_entry(
        str(tmp_path),
        entry_id=first["entry_id"],
        superseded_by_entry_id=second["entry_id"],
        retired_at="2026-08-31T00:00:00Z",
        key_provider=actor,
    )
    assert retired["stored"] is True
    assert retired["pending"] is False
    assert D.pending_count(str(tmp_path), key_provider=actor) == 0

    original_path, _entry = D._find_outbox_entry(
        str(tmp_path),
        first["entry_id"],
    )
    marker = json.loads(D._retirement_path(original_path).read_text())
    assert marker["reason_code"] == "superseded_by_acknowledged_request"
    assert G.verify_document_signature(
        marker,
        {G.signer_key_id(actor): actor.get_public_key()},
    ) == G.signer_key_id(actor)
    replay = D.retire_outbox_entry(
        str(tmp_path),
        entry_id=first["entry_id"],
        superseded_by_entry_id=second["entry_id"],
        key_provider=actor,
    )
    assert replay["stored"] is False
    assert replay["retirement_hash"] == retired["retirement_hash"]

    assert D.governed_work_delivery_status(
        str(tmp_path),
        key_provider=actor,
    ) == {
        "ok": True,
        "schema": "apatch.governed-work-delivery-status.v1",
        "queued": 1,
        "delivered": 1,
        "pending": 0,
        "projection_cursor": 0,
    }

    marker["retired_at"] = "2026-08-31T00:00:01Z"
    G._atomic_json(D._retirement_path(original_path), marker)
    with pytest.raises(G.GovernedWorkError, match="signature"):
        D.governed_work_delivery_status(
            str(tmp_path),
            key_provider=actor,
        )


def test_source_request_is_durable_offline_and_idempotent(tmp_path):
    actor, platform = Provider(), Provider()
    change = make_change(actor)
    first = D.queue_source_binding_request(
        str(tmp_path),
        change=change,
        client_id="apatch:test",
        created_at="2026-08-30T00:00:00Z",
    )
    second = D.queue_source_binding_request(
        str(tmp_path),
        change=change,
        client_id="apatch:test",
        created_at="2026-08-30T00:00:01Z",
    )
    assert first["stored"] is True
    assert second["stored"] is False
    assert D.pending_count(str(tmp_path)) == 1

    result = D.sync_governed_work(
        str(tmp_path),
        config=config(platform, actor),
        http_client=Client([]),
    )
    assert result["status"] == "credential_unavailable"
    raw = "\n".join(
        path.read_text()
        for path in (tmp_path / ".apatch/governed_work/outbox").glob("*.json")
    )
    assert "service_token" not in raw
    assert "APATCH_TEST_SERVICE_TOKEN" not in raw


def test_signed_source_binding_ack_is_verified_before_ack(tmp_path):
    actor, platform = Provider(), Provider()
    change = make_change(actor)
    queued = D.queue_source_binding_request(
        str(tmp_path),
        change=change,
        client_id="apatch:test",
        created_at="2026-08-30T00:00:00Z",
    )
    assert queued["request_hash"] == G.value_hash(
        {"command": "create_source_binding", "payload": change}
    )
    binding = make_binding(change, platform)
    client = Client([Response(binding)])
    result = D.sync_governed_work(
        str(tmp_path),
        config=config(platform, actor),
        request_key_provider=actor,
        http_client=client,
    )
    assert result == {
        "ok": True,
        "status": "in_sync",
        "delivered": 1,
        "pending": 0,
        "errors": [],
    }
    call = client.calls[0]
    assert "X-Service-Token" not in call["headers"]
    assert call["headers"]["X-TC-Service-Key-Id"] == T.service_request_key_id(actor)
    assert json.loads(call["content"]) == {
        "subject": "apatch:test",
        "tenant_id": change["tenant_id"],
        "change": change,
        "idempotency_key": f"source-binding:{change['change_id']}",
    }
    assert call["content"] == G.canonical_bytes(json.loads(call["content"]))
    assert D.governed_work_delivery_status(str(tmp_path))["projection_cursor"] == 0
    stored = G.load_project_source_binding(str(tmp_path), binding["binding_id"])
    assert stored == binding


def test_invalid_source_ack_preserves_outbox_for_retry(tmp_path):
    actor, platform = Provider(), Provider()
    change = make_change(actor)
    queued = D.queue_source_binding_request(
        str(tmp_path), change=change, client_id="apatch:test"
    )
    binding = make_binding(change, platform)
    invalid = dict(binding)
    invalid["spec_hash"] = "sha256:" + "f" * 64
    client = Client([Response(invalid)])
    result = D.sync_governed_work(
        str(tmp_path),
        config=config(platform, actor),
        request_key_provider=actor,
        http_client=client,
    )
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "ACK_VALIDATION_FAILED"
    assert D.pending_count(str(tmp_path)) == 1
    assert queued["pending"] is True


def test_evidence_requires_signed_platform_receipt_and_replays_safely(tmp_path):
    actor, platform = Provider(), Provider()
    change = make_change(actor)
    binding = make_binding(change, platform)
    evidence, timesheet = make_evidence(actor, binding)
    queued = D.queue_evidence_admission(
        str(tmp_path),
        evidence_bundle=evidence,
        timesheet_draft=timesheet,
        tenant_id=change["tenant_id"],
        project_group_id=change["project_group_id"],
        client_id="apatch:test",
        created_at="2026-08-30T00:12:00Z",
    )
    outbox_path = next(
        path
        for path in (tmp_path / ".apatch/governed_work/outbox").glob("*.json")
        if not path.name.endswith(".ack.json")
    )
    entry = json.loads(outbox_path.read_text())
    receipt = make_receipt(entry, evidence, platform)
    result = D.sync_governed_work(
        str(tmp_path),
        config=config(platform, actor),
        request_key_provider=actor,
        http_client=Client([Response(receipt)]),
    )
    assert queued["request_hash"] == G.value_hash(
        {
            "command": "admit_evidence",
            "payload": {
                "evidence_bundle": evidence,
                "timesheet_draft": timesheet,
            },
        }
    )
    assert result["ok"] is True
    assert result["delivered"] == 1
    assert D.pending_count(str(tmp_path)) == 0

    replay = D.queue_evidence_admission(
        str(tmp_path),
        evidence_bundle=evidence,
        timesheet_draft=timesheet,
        tenant_id=change["tenant_id"],
        project_group_id=change["project_group_id"],
        client_id="apatch:test",
    )
    assert replay["stored"] is False
    assert replay["pending"] is False
    assert replay["request_hash"] == queued["request_hash"]


def test_idempotency_key_conflict_fails_before_http(tmp_path):
    actor = Provider()
    change = make_change(actor)
    D.queue_source_binding_request(
        str(tmp_path), change=change, client_id="apatch:test"
    )
    path = next(
        path
        for path in (tmp_path / ".apatch/governed_work/outbox").glob("*.json")
        if not path.name.endswith(".ack.json")
    )
    raw = json.loads(path.read_text())
    raw["request_hash"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(raw))
    with pytest.raises(G.GovernedWorkError, match="idempotency key"):
        D.queue_source_binding_request(
            str(tmp_path), change=change, client_id="apatch:test"
        )
