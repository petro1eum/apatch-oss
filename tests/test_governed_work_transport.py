from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import governed_work as G
from apatch import governed_work_transport as T


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "governed_work"
    / "platform-ce540-service-request.json"
)


class Provider:
    def __init__(self, seed: bytes):
        self.private = Ed25519PrivateKey.from_private_bytes(seed)

    def get_public_key(self) -> bytes:
        return self.private.public_key().public_bytes_raw()

    def sign(self, payload: bytes) -> bytes:
        return self.private.sign(payload)


def test_platform_ce540_signed_request_vector_is_byte_exact():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    provider = Provider(bytes.fromhex(fixture["private_seed_hex"]))
    headers = T.signed_service_headers(
        ".",
        method=fixture["method"],
        path=fixture["path"],
        raw_body=fixture["raw_body"].encode("utf-8"),
        tenant_id=fixture["tenant_id"],
        subject=fixture["subject"],
        expected_key_id=fixture["service_request_key_id"],
        issued_at=fixture["issued_at"],
        nonce=fixture["nonce"],
        key_provider=provider,
    )
    assert headers == {
        "X-TC-Service-Key-Id": fixture["service_request_key_id"],
        "X-TC-Service-Issued-At": str(fixture["issued_at"]),
        "X-TC-Service-Expires-At": str(fixture["expires_at"]),
        "X-TC-Service-Nonce": fixture["nonce"],
        "X-TC-Service-Signature": fixture["signature"],
    }

    envelope = {
        "schema": T.SERVICE_REQUEST_SCHEMA,
        "method": fixture["method"],
        "path": fixture["path"],
        "body_sha256": fixture["body_sha256"],
        "tenant_id": fixture["tenant_id"],
        "subject": fixture["subject"],
        "issued_at": fixture["issued_at"],
        "expires_at": fixture["expires_at"],
        "nonce": fixture["nonce"],
    }
    provider.private.public_key().verify(
        base64.urlsafe_b64decode(fixture["signature"] + "=="),
        T.SERVICE_REQUEST_DOMAIN + G.canonical_bytes(envelope),
    )


def test_public_identity_exposes_only_platform_registration_material():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    provider = Provider(bytes.fromhex(fixture["private_seed_hex"]))
    identity = T.service_request_identity(".", key_provider=provider)
    assert identity == {
        "document_key_id": fixture["document_key_id"],
        "service_request_key_id": fixture["service_request_key_id"],
        "public_key": fixture["public_key"],
        "governed_work_public_keys": {
            purpose: {fixture["document_key_id"]: fixture["public_key"]}
            for purpose in T.APATCH_DOCUMENT_PURPOSES
        },
        "service_request_public_keys": {
            fixture["service_request_key_id"]: fixture["public_key"]
        },
    }
    assert "private" not in json.dumps(identity).casefold()
    assert fixture["private_seed_hex"] not in json.dumps(identity)


def test_request_signature_binds_empty_get_body_path_and_scope():
    provider = Provider(bytes(range(32)))
    key_id = T.service_request_key_id(provider)
    path = (
        "/api/internal/project-groups/"
        "tcpg_22222222222222222222222222222222/governed-work/status/"
        "tcwp_33333333333333333333333333333333"
    )
    headers = T.signed_service_headers(
        ".",
        method="GET",
        path=path,
        raw_body=b"",
        tenant_id="tenant-a",
        subject="apatch:test",
        expected_key_id=key_id,
        issued_at=1788100000,
        nonce="b" * 32,
        key_provider=provider,
    )
    envelope = {
        "schema": T.SERVICE_REQUEST_SCHEMA,
        "method": "GET",
        "path": path,
        "body_sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
        "tenant_id": "tenant-a",
        "subject": "apatch:test",
        "issued_at": 1788100000,
        "expires_at": 1788100020,
        "nonce": "b" * 32,
    }
    provider.private.public_key().verify(
        base64.urlsafe_b64decode(headers["X-TC-Service-Signature"] + "=="),
        T.SERVICE_REQUEST_DOMAIN + G.canonical_bytes(envelope),
    )


def test_request_signing_fails_closed_on_key_path_or_nonce_mismatch():
    provider = Provider(bytes(range(32)))
    with pytest.raises(G.GovernedWorkError, match="does not match"):
        T.signed_service_headers(
            ".",
            method="POST",
            path="/exact",
            raw_body=b"{}",
            tenant_id="tenant-a",
            subject="apatch:test",
            expected_key_id="sha256:" + "0" * 64,
            key_provider=provider,
        )
    with pytest.raises(G.GovernedWorkError, match="absolute URL path"):
        T.signed_service_headers(
            ".",
            method="POST",
            path="/exact?tenant=leak",
            raw_body=b"{}",
            tenant_id="tenant-a",
            subject="apatch:test",
            expected_key_id=T.service_request_key_id(provider),
            key_provider=provider,
        )
    with pytest.raises(G.GovernedWorkError, match="32 lowercase hex"):
        T.signed_service_headers(
            ".",
            method="POST",
            path="/exact",
            raw_body=b"{}",
            tenant_id="tenant-a",
            subject="apatch:test",
            expected_key_id=T.service_request_key_id(provider),
            nonce="not-canonical",
            key_provider=provider,
        )
