"""Signed external acceptance fact bound to one Avatar WorkEpisode.

An OutcomeAttestation records whether the natural counterparty that received or
measured a result accepted one concrete WorkEpisode.  It is deliberately
narrower than a professional credential.  Associations and assessors may issue
separate professional recognition, but cannot substitute for the work
counterparty.  Capability inference remains in apatch; valuation remains in HC
Capital and is determined through market transactions.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set


OUTCOME_ATTESTATION_SCHEMA_VERSION = 2
OUTCOME_ATTESTATION_SUPPORTED_SCHEMA_VERSIONS = (1, 2)
OUTCOME_ATTESTATION_KIND = "outcome_attestation"
OUTCOME_ATTESTATION_STATUSES = ("passed", "failed")
OUTCOME_ATTESTATION_BASIS = "external_acceptance"
# Retained for wire compatibility with schema v1 signatures.  The namespace is
# historical; it does not grant Associations authority over work acceptance.
OUTCOME_ATTESTATION_CHAIN_ID = "association_avatar_outcomes"
OUTCOME_ATTESTATION_EVENT = "OUTCOME_ATTESTATION_ISSUED"
OUTCOME_ACCEPTANCE_ROLES = frozenset(
    {
        "work_counterparty",
        "client_counterparty",
        "employer_counterparty",
        "competition_operator",
        "market_platform_counterparty",
    }
)
PROFESSIONAL_RECOGNITION_ROLES = frozenset(
    {
        "professional_association",
        "accredited_assessor",
        "independent_association",
        "independent_verifier",
    }
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:@#+/-]+$")
_DIGEST_40 = re.compile(r"^[0-9a-f]{40}$")
_TOP_LEVEL_KEYS_V1 = {
    "schema_version",
    "kind",
    "attestation_id",
    "request_id",
    "subject_avatar_id",
    "work_episode_id",
    "task",
    "outcome",
    "evidence_ref",
    "verifier",
    "issued_at",
    "trustchain_audit",
}
_TOP_LEVEL_KEYS_V2 = _TOP_LEVEL_KEYS_V1 | {"review_package_id"}
_ECONOMIC_KEYS: Set[str] = {
    "amount",
    "bonus",
    "clearing",
    "creator_bonus",
    "currency",
    "escrow",
    "gpi",
    "market_value",
    "mv",
    "pi",
    "portability_index",
    "price",
    "salary",
    "scarcity",
    "scarcity_index",
    "valuation",
}
_CONTENT_KEYS: Set[str] = {
    "command",
    "customer_data",
    "file_content",
    "private_prompt",
    "prompt",
    "raw_log",
    "raw_work",
    "secret",
    "source_code",
}


class OutcomeAttestationError(ValueError):
    """Raised when an external outcome violates the shared contract."""


def assert_outcome_acceptance_authority(
    verifier: Dict[str, Any],
    *,
    expected_organization_id: Optional[str] = None,
    authorized_roles: Optional[Set[str]] = None,
) -> None:
    """Require a purpose-bound natural counterparty for capability use.

    Parsing remains backward compatible so historical Association-issued facts
    stay auditable.  Consumers must call this guard before treating a fact as
    external acceptance.  ``authorized_roles`` narrows the canonical role set;
    it can never add a role that the shared contract does not recognize.
    """
    if not isinstance(verifier, dict):
        raise OutcomeAttestationError("verifier must be an object")
    organization_id = str(verifier.get("organization_id") or "")
    role = str(verifier.get("role") or "")
    configured_roles = set(authorized_roles or OUTCOME_ACCEPTANCE_ROLES)
    unknown = configured_roles - OUTCOME_ACCEPTANCE_ROLES
    if unknown:
        raise OutcomeAttestationError(
            f"configured roles cannot accept work outcomes: {sorted(unknown)}"
        )
    if role in PROFESSIONAL_RECOGNITION_ROLES:
        raise OutcomeAttestationError(
            "professional recognition cannot replace acceptance by the work counterparty"
        )
    if role not in OUTCOME_ACCEPTANCE_ROLES:
        raise OutcomeAttestationError("verifier role cannot accept a work outcome")
    if role not in configured_roles:
        raise OutcomeAttestationError(
            "outcome signer is not authorized for the declared role"
        )
    if expected_organization_id is not None and organization_id != str(
        expected_organization_id
    ):
        raise OutcomeAttestationError(
            "outcome signer is not authorized for the declared organization"
        )


def _canonical(value: Any, *, ensure_ascii: bool = False) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ensure_ascii,
    )


def outcome_attestation_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return the exact object covered by the TrustChain signature."""
    return {key: value for key, value in raw.items() if key != "trustchain_audit"}


def _identity_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key not in {"attestation_id", "trustchain_audit"}
    }


def compute_outcome_attestation_id(raw: Dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical(_identity_payload(raw)).encode("utf-8")
    ).hexdigest()[:40]


def _payload_hash(payload: Dict[str, Any]) -> str:
    # shared.trustchain_messages uses json.dumps' ASCII-safe default.
    safe = json.loads(json.dumps(payload, sort_keys=True, default=str))
    return hashlib.sha256(
        _canonical(safe, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _require_iso(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise OutcomeAttestationError(f"{field_name} must be ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OutcomeAttestationError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise OutcomeAttestationError(f"{field_name} must include a timezone")


def _require_safe_id(value: Any, field_name: str, *, digest: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise OutcomeAttestationError(f"{field_name} is invalid")
    pattern = _DIGEST_40 if digest else _SAFE_ID
    if not pattern.fullmatch(value):
        raise OutcomeAttestationError(f"{field_name} is not a content-safe identifier")
    return value


def _scan_forbidden(value: Any) -> Set[str]:
    found: Set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in _ECONOMIC_KEYS | _CONTENT_KEYS:
                found.add(key.lower())
            found |= _scan_forbidden(child)
    elif isinstance(value, list):
        for child in value:
            found |= _scan_forbidden(child)
    return found


def assert_outcome_attestation_content_safe(raw: Dict[str, Any]) -> None:
    found = _scan_forbidden(outcome_attestation_payload(raw))
    if found:
        raise OutcomeAttestationError(
            f"forbidden fields in OutcomeAttestation: {sorted(found)}"
        )


def verify_outcome_attestation_signature(
    raw: Dict[str, Any],
    *,
    trusted_public_key: Optional[str] = None,
) -> bool:
    """Verify exact payload binding and the portable TrustChain Ed25519 proof.

    ``trusted_public_key`` pins the outcome issuer. Passing no key verifies
    cryptographic integrity only; remote consumers must additionally bind that
    key to an organization and canonical acceptance roles.
    """
    try:
        audit = raw["trustchain_audit"]
        payload = outcome_attestation_payload(raw)
        if not isinstance(audit, dict):
            return False
        if audit.get("chain_id") != OUTCOME_ATTESTATION_CHAIN_ID:
            return False
        if audit.get("event") != OUTCOME_ATTESTATION_EVENT:
            return False
        if audit.get("algorithm") != "ed25519":
            return False
        if audit.get("payload_hash") != _payload_hash(payload):
            return False
        if trusted_public_key and audit.get("public_key") != trusted_public_key:
            return False
        signed = audit.get("signed_response")
        if not isinstance(signed, dict) or signed.get("data") != payload:
            return False
        if signed.get("tool_id") != (
            f"{OUTCOME_ATTESTATION_CHAIN_ID}:{OUTCOME_ATTESTATION_EVENT}"
        ):
            return False
        if signed.get("signature") != audit.get("signature"):
            return False
        if signed.get("alg") != audit.get("algorithm"):
            return False
        if float(signed.get("timestamp") or 0) != float(audit.get("signed_at") or 0):
            return False

        from trustchain import TrustChainVerifier

        result = TrustChainVerifier(
            str(trusted_public_key or audit.get("public_key") or ""),
            key_id=str(audit.get("key_id") or ""),
            max_age_seconds=None,
        ).verify(signed)
        return bool(result.valid)
    except Exception:
        return False


@dataclass(frozen=True)
class OutcomeAttestation:
    schema_version: int
    kind: str
    attestation_id: str
    request_id: str
    subject_avatar_id: str
    work_episode_id: str
    review_package_id: Optional[str]
    task: Dict[str, Any]
    outcome: Dict[str, Any]
    evidence_ref: str
    verifier: Dict[str, Any]
    issued_at: str
    trustchain_audit: Dict[str, Any]

    @classmethod
    def from_wire(
        cls,
        raw: Dict[str, Any],
        *,
        verify_signature: bool = False,
        trusted_public_key: Optional[str] = None,
    ) -> "OutcomeAttestation":
        if not isinstance(raw, dict):
            raise OutcomeAttestationError("OutcomeAttestation root must be an object")
        schema_version = raw.get("schema_version")
        expected_keys = (
            _TOP_LEVEL_KEYS_V2
            if schema_version == OUTCOME_ATTESTATION_SCHEMA_VERSION
            else _TOP_LEVEL_KEYS_V1
        )
        unknown = set(raw) - expected_keys
        missing = expected_keys - set(raw)
        if unknown:
            raise OutcomeAttestationError(f"unknown OutcomeAttestation fields: {sorted(unknown)}")
        if missing:
            raise OutcomeAttestationError(f"missing OutcomeAttestation fields: {sorted(missing)}")
        assert_outcome_attestation_content_safe(raw)
        if raw["schema_version"] not in OUTCOME_ATTESTATION_SUPPORTED_SCHEMA_VERSIONS:
            raise OutcomeAttestationError("OutcomeAttestation schema_version is invalid")
        if raw["kind"] != OUTCOME_ATTESTATION_KIND:
            raise OutcomeAttestationError("OutcomeAttestation kind is invalid")
        _require_safe_id(raw["attestation_id"], "attestation_id", digest=True)
        if raw["attestation_id"] != compute_outcome_attestation_id(raw):
            raise OutcomeAttestationError("attestation_id does not match content")
        _require_safe_id(raw["request_id"], "request_id")
        _require_safe_id(raw["subject_avatar_id"], "subject_avatar_id")
        _require_safe_id(raw["work_episode_id"], "work_episode_id", digest=True)
        if raw["schema_version"] >= 2:
            _require_safe_id(
                raw["review_package_id"],
                "review_package_id",
                digest=True,
            )
        _require_safe_id(raw["evidence_ref"], "evidence_ref")
        _require_iso(raw["issued_at"], "issued_at")

        task = raw["task"]
        if not isinstance(task, dict) or set(task) != {"label", "taxonomy_refs"}:
            raise OutcomeAttestationError("task fields are invalid")
        if not isinstance(task["label"], str) or not task["label"] or len(task["label"]) > 240:
            raise OutcomeAttestationError("task.label is invalid")
        refs = task["taxonomy_refs"]
        if not isinstance(refs, list) or not refs or len(refs) > 100:
            raise OutcomeAttestationError("task.taxonomy_refs must be a non-empty array")
        for ref in refs:
            _require_safe_id(ref, "task.taxonomy_refs")

        outcome = raw["outcome"]
        if not isinstance(outcome, dict) or set(outcome) != {
            "status", "basis", "observed_at"
        }:
            raise OutcomeAttestationError("outcome fields are invalid")
        if outcome["status"] not in OUTCOME_ATTESTATION_STATUSES:
            raise OutcomeAttestationError("outcome.status is invalid")
        if outcome["basis"] != OUTCOME_ATTESTATION_BASIS:
            raise OutcomeAttestationError("outcome.basis is invalid")
        _require_iso(outcome["observed_at"], "outcome.observed_at")

        verifier = raw["verifier"]
        if not isinstance(verifier, dict) or set(verifier) != {
            "organization_id", "role", "decision_event_id"
        }:
            raise OutcomeAttestationError("verifier fields are invalid")
        _require_safe_id(verifier["organization_id"], "verifier.organization_id")
        _require_safe_id(verifier["role"], "verifier.role")
        _require_safe_id(verifier["decision_event_id"], "verifier.decision_event_id")

        if not isinstance(raw["trustchain_audit"], dict):
            raise OutcomeAttestationError("trustchain_audit must be an object")
        if verify_signature and not verify_outcome_attestation_signature(
            raw,
            trusted_public_key=trusted_public_key,
        ):
            raise OutcomeAttestationError("OutcomeAttestation signature verification failed")
        normalized = dict(raw)
        normalized.setdefault("review_package_id", None)
        return cls(**normalized)

    def to_wire(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


def build_outcome_attestation_payload(
    *,
    request_id: str,
    subject_avatar_id: str,
    work_episode_id: str,
    review_package_id: str,
    task_label: str,
    taxonomy_refs: List[str],
    outcome_status: str,
    observed_at: str,
    evidence_ref: str,
    verifier_organization_id: str,
    verifier_role: str,
    decision_event_id: str,
    issued_at: str,
) -> Dict[str, Any]:
    """Build the unsigned payload passed to ``sign_message_payload``."""
    _require_safe_id(review_package_id, "review_package_id", digest=True)
    raw: Dict[str, Any] = {
        "schema_version": OUTCOME_ATTESTATION_SCHEMA_VERSION,
        "kind": OUTCOME_ATTESTATION_KIND,
        "request_id": request_id,
        "subject_avatar_id": subject_avatar_id,
        "work_episode_id": work_episode_id,
        "review_package_id": review_package_id,
        "task": {
            "label": task_label,
            "taxonomy_refs": sorted(set(taxonomy_refs)),
        },
        "outcome": {
            "status": outcome_status,
            "basis": OUTCOME_ATTESTATION_BASIS,
            "observed_at": observed_at,
        },
        "evidence_ref": evidence_ref,
        "verifier": {
            "organization_id": verifier_organization_id,
            "role": verifier_role,
            "decision_event_id": decision_event_id,
        },
        "issued_at": issued_at,
    }
    raw["attestation_id"] = compute_outcome_attestation_id(raw)
    return raw
