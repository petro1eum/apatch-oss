from __future__ import annotations

import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import governed_work as G
from apatch import governed_work_delivery as D
from apatch import governed_work_transport as T
from apatch import work_item_acceptance as W

TENANT = "11111111-1111-4111-8111-111111111111"
GROUP = "tcpg_" + "2" * 32
ITEM = "tcpwi_" + "3" * 32
PROGRAM = "tcwp_" + "4" * 32


class Provider:
    def __init__(self):
        self.private = Ed25519PrivateKey.generate()

    def get_public_key(self):
        return self.private.public_key().public_bytes_raw()

    def sign(self, payload):
        return self.private.sign(payload)


class Response:
    status_code = 201

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class Client:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def post(self, url, content, headers):
        self.calls.append({"url": url, "content": content, "headers": headers})
        return Response(self.body)


def _platform_signed(body, provider, *, purpose=W.BINDING_PURPOSE):
    return {
        **body,
        "signature": {
            "algorithm": "Ed25519",
            "key_id": G.signer_key_id(provider),
            "value": G._b64url_encode(provider.sign(G._platform_signature_payload(body, purpose=purpose))),
        },
    }


def _documents(actor):
    change = G.sign_document({
        "schema": G.CHANGE_SCHEMA, "change_id": "apchg_" + "5" * 32,
        "execution_system": "apatch", "tenant_id": TENANT, "project_group_id": GROUP,
        "source_kind": "work_program", "work_program_id": PROGRAM,
        "work_program_hash": "sha256:" + "6" * 64, "context_release_id": None,
        "context_release_manifest_hash": None, "spec_id": "SPEC-LOCAL-1",
        "spec_hash": "sha256:" + "7" * 64,
        "requirement_refs": [{"requirement_id": "R1", "requirement_hash": "sha256:" + "8" * 16}],
        "purpose_hash": "sha256:" + "9" * 64, "actor_key_id": G.signer_key_id(actor),
        "issued_at": "2026-09-11T12:00:00Z",
    }, actor)
    acceptance = G.sign_document({
        "schema": W.ACCEPTANCE_SCHEMA, "intent_id": "tcapsei_" + "a" * 32,
        "proposal_document_hash": "sha256:" + "b" * 64,
        "proposal_envelope_hash": "sha256:" + "c" * 64, "tenant_id": TENANT,
        "project_group_id": GROUP, "work_item_id": ITEM,
        "work_item_hash": "sha256:" + "d" * 64, "authority_version": 7,
        "work_program_id": PROGRAM, "work_program_hash": change["work_program_hash"],
        "change_id": change["change_id"], "change_hash": G.document_hash(change),
        "accepted_at": "2026-09-11T12:01:00Z",
    }, actor)
    return change, acceptance


def _binding(platform, change, acceptance):
    return _platform_signed({
        "schema": W.BINDING_SCHEMA, "binding_id": "tcawieb_" + "e" * 32,
        "tenant_id": TENANT, "project_group_id": GROUP, "work_item_id": ITEM,
        "accepted_work_item_hash": acceptance["work_item_hash"], "accepted_authority_version": 7,
        "current_work_item_hash": "sha256:" + "f" * 64, "current_authority_version": 8,
        "work_program_id": PROGRAM, "work_program_hash": change["work_program_hash"],
        "intent_id": acceptance["intent_id"], "intent_document_hash": acceptance["proposal_document_hash"],
        "intent_envelope_hash": acceptance["proposal_envelope_hash"],
        "proposal_acceptance_hash": G.document_hash(acceptance), "change_id": change["change_id"],
        "change_hash": G.document_hash(change),
        "actor_ref": "member:" + "1" * 32,
        "binding_authority_version": 1, "accepted_at": acceptance["accepted_at"],
    }, platform)


def _config(actor, platform):
    return {
        "schema": D.CONFIG_SCHEMA, "platform_url": "https://platform.example",
        "client_id": TENANT, "service_request_key_id": T.service_request_key_id(actor),
        "binding_authority_keys": {G.signer_key_id(platform): G._b64url_encode(platform.get_public_key())},
    }


def _source_binding(platform, change):
    return _platform_signed({
        "schema": G.SOURCE_BINDING_SCHEMA,
        "binding_id": "tcpsb_" + "0" * 32,
        "tenant_id": TENANT,
        "project_group_id": GROUP,
        "source_kind": "work_program",
        "work_program_id": PROGRAM,
        "work_program_hash": change["work_program_hash"],
        "context_release_id": None,
        "context_release_manifest_hash": None,
        "execution_system": "apatch",
        "change_id": change["change_id"],
        "change_hash": G.document_hash(change),
        "spec_id": change["spec_id"],
        "spec_hash": change["spec_hash"],
        "requirement_refs": change["requirement_refs"],
        "actor_ref": "member:" + "1" * 32,
        "authority_version": 2,
        "issued_at": "2026-09-11T12:02:00Z",
    }, platform, purpose=G.PLATFORM_SOURCE_BINDING_PURPOSE)


def test_exact_acceptance_chains_to_source_binding_without_claiming_it_early(tmp_path):
    actor, platform = Provider(), Provider()
    change, acceptance = _documents(actor)
    queued = D.queue_work_item_acceptance(str(tmp_path), proposal_acceptance=acceptance, change=change, client_id=TENANT)
    binding = _binding(platform, change, acceptance)
    client = Client(binding)
    result = D.sync_governed_work(str(tmp_path), config=_config(actor, platform), request_key_provider=actor, http_client=client)
    assert result == {"ok": True, "status": "delivery_incomplete", "delivered": 1, "pending": 1, "errors": []}
    assert queued["pending"] is True
    call = client.calls[0]
    assert call["url"].endswith(f"/api/internal/client/project-groups/{GROUP}/work-items/{ITEM}/apatch-studio/acceptances")
    assert json.loads(call["content"]) == {
        "subject": TENANT, "tenant_id": TENANT, "proposal_acceptance": acceptance,
        "change": change, "idempotency_key": f"work-item-acceptance:{change['change_id']}",
    }
    assert W.binding_path(str(tmp_path), binding["binding_id"]).is_file()
    assert D.pending_count(str(tmp_path)) == 1
    commands = [entry["command"] for _path, entry in D._iter_outbox(str(tmp_path))]
    assert commands == ["work_item_acceptance", "source_binding"]

    source = _source_binding(platform, change)
    source_client = Client(source)
    second = D.sync_governed_work(
        str(tmp_path),
        config=_config(actor, platform),
        request_key_provider=actor,
        http_client=source_client,
    )
    assert second == {"ok": True, "status": "in_sync", "delivered": 1, "pending": 0, "errors": []}
    assert source_client.calls[0]["url"].endswith(
        f"/api/internal/project-groups/{GROUP}/governed-work/source-bindings"
    )
    assert G.load_project_source_binding(str(tmp_path), source["binding_id"]) == source

    replay = D.sync_governed_work(
        str(tmp_path),
        config=_config(actor, platform),
        request_key_provider=actor,
        http_client=Client({}),
    )
    assert replay == {"ok": True, "status": "in_sync", "delivered": 0, "pending": 0, "errors": []}


def test_tampered_or_cross_linked_binding_stays_pending(tmp_path):
    actor, platform = Provider(), Provider()
    change, acceptance = _documents(actor)
    D.queue_work_item_acceptance(str(tmp_path), proposal_acceptance=acceptance, change=change, client_id=TENANT)
    binding = _binding(platform, change, acceptance)
    binding["current_work_item_hash"] = "sha256:" + "0" * 64
    result = D.sync_governed_work(str(tmp_path), config=_config(actor, platform), request_key_provider=actor, http_client=Client(binding))
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "ACK_VALIDATION_FAILED"
    assert D.pending_count(str(tmp_path)) == 1
    commands = [entry["command"] for _path, entry in D._iter_outbox(str(tmp_path))]
    assert commands == ["work_item_acceptance"]
