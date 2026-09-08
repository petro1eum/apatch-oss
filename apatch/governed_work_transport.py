"""Purpose-separated Ed25519 authentication for TrustChain governed-work HTTP."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from typing import Any, Dict, Optional

from apatch import governed_work as G


SERVICE_REQUEST_SCHEMA = "trustchain.project-group.service-request.v1"
SERVICE_REQUEST_DOMAIN = b"TrustChain-ProjectGroup-Service-Request\x00v1\x00"
SERVICE_REQUEST_LIFETIME_SECONDS = 20
APATCH_DOCUMENT_PURPOSES = (
    "apatch.governed_work.change",
    "apatch.governed_work.evidence",
    "apatch.governed_work.timesheet",
)

_KEY_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")


def _public_bytes(key_provider: Any) -> bytes:
    try:
        public = key_provider.get_public_key()
    except Exception as exc:
        raise G.GovernedWorkError(
            "an enrolled APatch Ed25519 identity is required"
        ) from exc
    if not isinstance(public, bytes) or len(public) != 32:
        raise G.GovernedWorkError("Ed25519 public key must be 32 raw bytes")
    return public


def _load_provider(target_dir: str, key_provider: Any = None) -> Any:
    if key_provider is not None:
        _public_bytes(key_provider)
        return key_provider
    from apatch.trust_identity import load_local_identity

    identity = load_local_identity(target_dir)
    provider = getattr(identity, "key_provider", None) if identity else None
    if provider is None:
        raise G.GovernedWorkError(
            "an enrolled APatch Ed25519 identity is required for Platform requests"
        )
    _public_bytes(provider)
    return provider


def service_request_key_id(key_provider: Any) -> str:
    return "sha256:" + hashlib.sha256(_public_bytes(key_provider)).hexdigest()


def service_request_identity(
    target_dir: str = ".",
    *,
    key_provider: Any = None,
) -> Dict[str, Any]:
    provider = _load_provider(target_dir, key_provider)
    public = _public_bytes(provider)
    public_encoded = base64.urlsafe_b64encode(public).rstrip(b"=").decode("ascii")
    document_key_id = G.signer_key_id(provider)
    request_key_id = service_request_key_id(provider)
    document_mapping = {
        purpose: {document_key_id: public_encoded}
        for purpose in APATCH_DOCUMENT_PURPOSES
    }
    return {
        "document_key_id": document_key_id,
        "service_request_key_id": request_key_id,
        "public_key": public_encoded,
        "governed_work_public_keys": document_mapping,
        "service_request_public_keys": {request_key_id: public_encoded},
    }


def signed_service_headers(
    target_dir: str,
    *,
    method: str,
    path: str,
    raw_body: bytes,
    tenant_id: str,
    subject: str,
    expected_key_id: str,
    issued_at: Optional[int] = None,
    nonce: Optional[str] = None,
    key_provider: Any = None,
) -> Dict[str, str]:
    provider = _load_provider(target_dir, key_provider)
    actual_key_id = service_request_key_id(provider)
    if not _KEY_ID_RE.fullmatch(str(expected_key_id)) or actual_key_id != expected_key_id:
        raise G.GovernedWorkError(
            "configured service request key does not match the enrolled APatch identity"
        )
    normalized_method = str(method).upper()
    if normalized_method not in {"GET", "POST"}:
        raise G.GovernedWorkError("unsupported governed-work HTTP method")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or "?" in path
        or "#" in path
    ):
        raise G.GovernedWorkError("service request path must be an absolute URL path")
    if not isinstance(raw_body, bytes):
        raise G.GovernedWorkError("service request body must be exact bytes")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise G.GovernedWorkError("service request tenant_id is required")
    if not isinstance(subject, str) or not subject:
        raise G.GovernedWorkError("service request subject is required")

    issued = int(time.time()) if issued_at is None else int(issued_at)
    expires = issued + SERVICE_REQUEST_LIFETIME_SECONDS
    request_nonce = nonce or secrets.token_hex(16)
    if not _NONCE_RE.fullmatch(request_nonce):
        raise G.GovernedWorkError("service request nonce must be 32 lowercase hex")

    envelope = {
        "schema": SERVICE_REQUEST_SCHEMA,
        "method": normalized_method,
        "path": path,
        "body_sha256": "sha256:" + hashlib.sha256(raw_body).hexdigest(),
        "tenant_id": tenant_id,
        "subject": subject,
        "issued_at": issued,
        "expires_at": expires,
        "nonce": request_nonce,
    }
    try:
        signature = provider.sign(SERVICE_REQUEST_DOMAIN + G.canonical_bytes(envelope))
    except Exception as exc:
        raise G.GovernedWorkError("service request signing failed") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise G.GovernedWorkError("service request signature must be 64 raw bytes")
    signature_encoded = (
        base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )
    return {
        "X-TC-Service-Key-Id": actual_key_id,
        "X-TC-Service-Issued-At": str(issued),
        "X-TC-Service-Expires-At": str(expires),
        "X-TC-Service-Nonce": request_nonce,
        "X-TC-Service-Signature": signature_encoded,
    }
