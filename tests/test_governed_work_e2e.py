from __future__ import annotations

import json

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
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class Client:
    def __init__(self, *bodies):
        self.responses = [Response(body) for body in bodies]
        self.calls = []

    def post(self, url, content, headers):
        self.calls.append({"url": url, "content": content, "headers": headers})
        return self.responses.pop(0)


def write_spec(root):
    path = root / "docs" / "specs" / "SPEC-E2E-1.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# SPEC-E2E-1 -- Governed work\n\n"
        "## R1 Deliver exact result\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )
    return path


def make_binding(change, platform):
    body = {
        "schema": G.SOURCE_BINDING_SCHEMA,
        "binding_id": "tcpsb_" + "4" * 32,
        "tenant_id": change["tenant_id"],
        "project_group_id": change["project_group_id"],
        "source_kind": change["source_kind"],
        "work_program_id": change["work_program_id"],
        "work_program_hash": change["work_program_hash"],
        "context_release_id": change["context_release_id"],
        "context_release_manifest_hash": change[
            "context_release_manifest_hash"
        ],
        "execution_system": "apatch",
        "change_id": change["change_id"],
        "change_hash": G.document_hash(change),
        "spec_id": change["spec_id"],
        "spec_hash": change["spec_hash"],
        "requirement_refs": change["requirement_refs"],
        "actor_ref": "agent_binding:tcpgab_" + "5" * 32,
        "authority_version": 1,
        "issued_at": "2026-08-30T00:01:00Z",
    }
    return platform_sign(
        body,
        platform,
        G.PLATFORM_SOURCE_BINDING_PURPOSE,
    )


def make_event():
    return {
        "schema_version": 3,
        "kind": "fact",
        "event_id": "event-e2e",
        "source": "apatch",
        "trust_level": "attested",
        "idempotency_key": "event-e2e",
        "avatar_id": "actor",
        "identity": {"key_id": "actor"},
        "project": {"id": "project"},
        "session": {
            "session_id": "session-R1",
            "started_at": "2026-08-30T00:02:00Z",
            "ended_at": "2026-08-30T00:12:00Z",
            "duration_sec": 600,
            "active_sec": 540,
            "artifacts": [],
            "intent": "",
        },
        "volume": {
            "ops": 1,
            "files_touched": 1,
            "insertions": 1,
            "deletions": 0,
        },
        "proof_ref": {
            "op_ids": ["op-1"],
            "head": "op-1",
            "committed_at": 1,
        },
        "payload": None,
        "created_at": "2026-08-30T00:12:00Z",
        "signature": "existing-contribution-signature",
    }


def config(platform, actor):
    return {
        "schema": D.CONFIG_SCHEMA,
        "platform_url": "https://platform.example",
        "client_id": "apatch:e2e",
        "service_request_key_id": T.service_request_key_id(actor),
        "binding_authority_keys": {
            G.signer_key_id(platform): G._b64url_encode(
                platform.get_public_key()
            )
        },
    }


def test_complete_chain_recovers_from_invalid_ack_and_replays_idempotently(
    tmp_path,
):
    actor, platform = Provider(), Provider()
    spec = write_spec(tmp_path)
    prepared = G.prepare_change(
        str(tmp_path),
        tenant_id="tenant-a",
        project_group_id="tcpg_" + "1" * 32,
        work_program_id="tcwp_" + "2" * 32,
        work_program_hash="sha256:" + "3" * 64,
        spec_id="SPEC-E2E-1",
        spec_path=str(spec),
        requirement_ids=["R1"],
        purpose="Private customer intent",
        issued_at="2026-08-30T00:00:00Z",
        key_provider=actor,
    )
    change = prepared["change"]
    replayed_change = G.prepare_change(
        str(tmp_path),
        tenant_id="tenant-a",
        project_group_id="tcpg_" + "1" * 32,
        work_program_id="tcwp_" + "2" * 32,
        work_program_hash="sha256:" + "3" * 64,
        spec_id="SPEC-E2E-1",
        spec_path=str(spec),
        requirement_ids=["R1"],
        purpose="Private customer intent",
        issued_at="2099-01-01T00:00:00Z",
        key_provider=actor,
    )
    assert replayed_change["stored"] is False
    assert replayed_change["change"] == change

    source_queue = D.queue_source_binding_request(
        str(tmp_path),
        change=change,
        client_id="apatch:e2e",
        created_at="2026-08-30T00:00:00Z",
    )
    binding = make_binding(change, platform)
    assert source_queue["request_hash"] == G.value_hash(
        {"command": "create_source_binding", "payload": change}
    )
    source_client = Client(binding)
    source_sync = D.sync_governed_work(
        str(tmp_path),
        config=config(platform, actor),
        request_key_provider=actor,
        http_client=source_client,
    )
    assert source_sync["status"] == "in_sync"
    assert G.load_project_source_binding(
        str(tmp_path), binding["binding_id"]
    ) == binding

    event = make_event()
    timesheet = G.build_timesheet_draft(
        str(tmp_path),
        binding=binding,
        sessions=[
            {
                "governed_session_id": "session-R1",
                "started_at": "2026-08-30T00:02:00Z",
                "ended_at": "2026-08-30T00:12:00Z",
            }
        ],
        contribution_events=[event],
        issued_at="2026-08-30T00:13:00Z",
        key_provider=actor,
    )["document"]
    evidence = G.build_work_evidence_bundle(
        str(tmp_path),
        change=change,
        binding=binding,
        attestation_facts=[
            {
                "spec_id": change["spec_id"],
                "requirement_id": "R1",
                "requirement_hash": change["requirement_refs"][0][
                    "requirement_hash"
                ],
                "attestation_id": "att-R1",
                "attestation_hash": "sha256:" + "6" * 64,
                "outcome": "passed",
                "project_source_binding_id": binding["binding_id"],
                "project_source_binding_hash": G.document_hash(binding),
                "governed_session_id": "session-R1",
            }
        ],
        contribution_events=[event],
        timesheet=timesheet,
        issued_at="2026-08-30T00:14:00Z",
        key_provider=actor,
    )["document"]
    evidence_queue = D.queue_evidence_admission(
        str(tmp_path),
        evidence_bundle=evidence,
        timesheet_draft=timesheet,
        tenant_id=change["tenant_id"],
        project_group_id=change["project_group_id"],
        client_id="apatch:e2e",
        created_at="2026-08-30T00:14:00Z",
    )
    receipt_body = {
        "schema": D.RECEIPT_SCHEMA,
        "receipt_id": "tcgwar_" + "7" * 32,
        "tenant_id": change["tenant_id"],
        "project_group_id": change["project_group_id"],
        "request_hash": evidence_queue["request_hash"],
        "command": "evidence_admission",
        "resource_id": evidence["bundle_id"],
        "resource_hash": G.document_hash(evidence),
        "projection_cursor": 9,
        "accepted_at": "2026-08-30T00:15:00Z",
    }
    receipt = platform_sign(
        receipt_body,
        platform,
        G.PLATFORM_EVIDENCE_ADMISSION_PURPOSE,
    )
    tampered_receipt = dict(receipt)
    tampered_receipt["resource_hash"] = "sha256:" + "0" * 64
    rejected = D.sync_governed_work(
        str(tmp_path),
        config=config(platform, actor),
        request_key_provider=actor,
        http_client=Client(tampered_receipt),
    )
    assert rejected["ok"] is False
    assert rejected["errors"][0]["code"] == "ACK_VALIDATION_FAILED"
    assert D.pending_count(str(tmp_path)) == 1

    recovered = D.sync_governed_work(
        str(tmp_path),
        config=config(platform, actor),
        request_key_provider=actor,
        http_client=Client(receipt),
    )
    assert recovered == {
        "ok": True,
        "status": "in_sync",
        "delivered": 1,
        "pending": 0,
        "errors": [],
    }
    replay = D.queue_evidence_admission(
        str(tmp_path),
        evidence_bundle=evidence,
        timesheet_draft=timesheet,
        tenant_id=change["tenant_id"],
        project_group_id=change["project_group_id"],
        client_id="apatch:e2e",
    )
    assert replay["stored"] is False
    assert replay["pending"] is False
    assert replay["request_hash"] == evidence_queue["request_hash"]

    change_path = (
        tmp_path
        / ".apatch"
        / "governed_work"
        / "changes"
        / f"{change['change_id']}.json"
    )
    evidence_path = (
        tmp_path
        / ".apatch"
        / "governed_work"
        / "evidence"
        / f"{evidence['bundle_id']}.json"
    )
    assert change_path.read_bytes() == G.canonical_bytes(change)
    assert evidence_path.read_bytes() == G.canonical_bytes(evidence)
    assert evidence["change_hash"] == G.document_hash(change)
    assert evidence["project_source_binding_hash"] == G.document_hash(binding)
    assert evidence["timesheet_ref"]["timesheet_hash"] == G.document_hash(
        timesheet
    )

    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / ".apatch" / "governed_work").rglob("*.json")
    )
    assert "runtime-secret" not in persisted
    assert "Private customer intent" not in persisted
    for forbidden in (
        "source_code",
        "session_token",
        "/Users/",
        "credentials",
    ):
        assert forbidden not in json.dumps(evidence)
    assert D.governed_work_delivery_status(str(tmp_path)) == {
        "ok": True,
        "schema": "apatch.governed-work-delivery-status.v1",
        "queued": 2,
        "delivered": 2,
        "pending": 0,
        "projection_cursor": 9,
    }
