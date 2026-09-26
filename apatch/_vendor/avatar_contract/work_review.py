"""Content-safe package shown to a natural work counterparty before acceptance.

The package is a signed metadata distillate, not a work archive.  It says what
was requested, what was delivered, how the producer checked it and where the
proof pointers live.  A counterparty decision can bind to ``review_package_id``
without receiving source code, private prompts, credentials or economic data.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set


WORK_REVIEW_PACKAGE_SCHEMA_VERSION = 1
WORK_REVIEW_PACKAGE_STATUSES = ("ready", "incomplete")
WORK_REVIEW_SIGNATURE_STATES = ("verified", "missing", "invalid", "unverifiable")
WORK_REVIEW_GATE_STATES = ("falsified", "unprobed", "false_gate", "not_applicable")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:@#+/-]+$")
_DIGEST_40 = re.compile(r"^[0-9a-f]{40}$")
_PACKAGE_KEYS = {
    "schema_version",
    "review_package_id",
    "status",
    "objective",
    "delivery_summary",
    "acceptance_criteria",
    "verification",
    "artifact_refs",
    "limitations",
    "missing_fields",
}
_VERIFICATION_KEYS = {"source_signature", "gate", "proof_refs"}
_MISSING_FIELD_ORDER = (
    "objective",
    "delivery_summary",
    "acceptance_criteria",
    "artifact_refs",
    "source_signature",
    "proof_refs",
)
_ECONOMIC_KEYS: Set[str] = {
    "amount",
    "bonus",
    "clearing",
    "creator_bonus",
    "currency",
    "escrow",
    "gpi",
    "market_price",
    "market_value",
    "mv",
    "pi",
    "portability_index",
    "price",
    "salary",
    "scarcity_index",
    "valuation",
}
_CONTENT_KEYS: Set[str] = {
    "command",
    "credential",
    "customer_data",
    "file_content",
    "private_prompt",
    "prompt",
    "raw_log",
    "raw_work",
    "secret",
    "source_code",
}


class WorkReviewPackageError(ValueError):
    """Raised when a counterparty review package is unsafe or inconsistent."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _identity_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in raw.items() if key != "review_package_id"}


def compute_work_review_package_id(raw: Dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical(_identity_payload(raw)).encode("utf-8")
    ).hexdigest()[:40]


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


def _normalize_text(value: Any, *, max_length: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkReviewPackageError("review text must be a string or null")
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise WorkReviewPackageError(
            f"review text must be at most {max_length} characters"
        )
    return normalized


def _text_list(
    value: Any,
    field_name: str,
    *,
    max_items: int = 20,
    max_length: int = 300,
) -> List[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise WorkReviewPackageError(
            f"{field_name} must be an array with at most {max_items} items"
        )
    normalized: List[str] = []
    for item in value:
        text = _normalize_text(item, max_length=max_length)
        if text and text not in normalized:
            normalized.append(text)
    if normalized != value:
        raise WorkReviewPackageError(f"{field_name} must be normalized and unique")
    return normalized


def _safe_ids(value: Any, field_name: str, *, max_items: int = 100) -> List[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise WorkReviewPackageError(
            f"{field_name} must be an array with at most {max_items} items"
        )
    result: List[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 256
            or not _SAFE_ID.fullmatch(item)
        ):
            raise WorkReviewPackageError(f"{field_name} contains an unsafe identifier")
        if item not in result:
            result.append(item)
    if result != value:
        raise WorkReviewPackageError(f"{field_name} must be sorted and unique")
    return result


def _expected_missing(raw: Dict[str, Any]) -> List[str]:
    verification = raw.get("verification") or {}
    missing: List[str] = []
    if raw.get("objective") is None:
        missing.append("objective")
    if raw.get("delivery_summary") is None:
        missing.append("delivery_summary")
    if not raw.get("acceptance_criteria"):
        missing.append("acceptance_criteria")
    if not raw.get("artifact_refs"):
        missing.append("artifact_refs")
    if verification.get("source_signature") != "verified":
        missing.append("source_signature")
    if not verification.get("proof_refs"):
        missing.append("proof_refs")
    return [name for name in _MISSING_FIELD_ORDER if name in missing]


def validate_work_review_package(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and return a detached copy of one review package."""
    if not isinstance(raw, dict) or set(raw) != _PACKAGE_KEYS:
        raise WorkReviewPackageError("WorkReviewPackage fields are invalid")
    forbidden = _scan_forbidden(raw)
    if forbidden:
        raise WorkReviewPackageError(
            f"forbidden fields in WorkReviewPackage: {sorted(forbidden)}"
        )
    if raw["schema_version"] != WORK_REVIEW_PACKAGE_SCHEMA_VERSION:
        raise WorkReviewPackageError("WorkReviewPackage schema_version is invalid")
    package_id = raw["review_package_id"]
    if not isinstance(package_id, str) or not _DIGEST_40.fullmatch(package_id):
        raise WorkReviewPackageError("review_package_id must be a 40-character digest")
    if package_id != compute_work_review_package_id(raw):
        raise WorkReviewPackageError("review_package_id does not match package content")

    objective = _normalize_text(raw["objective"], max_length=500)
    delivered = _normalize_text(raw["delivery_summary"], max_length=600)
    if objective != raw["objective"] or delivered != raw["delivery_summary"]:
        raise WorkReviewPackageError("review summaries must be normalized")
    _text_list(raw["acceptance_criteria"], "acceptance_criteria")
    _text_list(raw["limitations"], "limitations")
    _safe_ids(raw["artifact_refs"], "artifact_refs")

    verification = raw["verification"]
    if not isinstance(verification, dict) or set(verification) != _VERIFICATION_KEYS:
        raise WorkReviewPackageError("verification fields are invalid")
    if verification["source_signature"] not in WORK_REVIEW_SIGNATURE_STATES:
        raise WorkReviewPackageError("verification.source_signature is invalid")
    if verification["gate"] not in WORK_REVIEW_GATE_STATES:
        raise WorkReviewPackageError("verification.gate is invalid")
    _safe_ids(verification["proof_refs"], "verification.proof_refs")

    expected_missing = _expected_missing(raw)
    if raw["missing_fields"] != expected_missing:
        raise WorkReviewPackageError("missing_fields does not match package content")
    expected_status = "ready" if not expected_missing else "incomplete"
    if raw["status"] != expected_status:
        raise WorkReviewPackageError("review package status does not match completeness")
    return json.loads(json.dumps(raw))


def _normalized_list(values: Iterable[Any], *, safe: bool = False) -> List[str]:
    result: List[str] = []
    for value in values:
        if safe:
            text = str(value or "").strip()
            if not text or len(text) > 256 or not _SAFE_ID.fullmatch(text):
                continue
        else:
            text = _normalize_text(value, max_length=300) or ""
            if not text:
                continue
        if text not in result:
            result.append(text)
    return sorted(result)


def build_work_review_package(
    *,
    objective: Optional[str],
    delivery_summary: Optional[str],
    acceptance_criteria: Iterable[str],
    source_signature: str,
    gate: str,
    proof_refs: Iterable[str],
    artifact_refs: Iterable[str],
    limitations: Iterable[str] = (),
) -> Dict[str, Any]:
    """Build a deterministic package; missing evidence is explicit, never guessed."""
    raw: Dict[str, Any] = {
        "schema_version": WORK_REVIEW_PACKAGE_SCHEMA_VERSION,
        "review_package_id": "",
        "status": "incomplete",
        "objective": _normalize_text(objective, max_length=500),
        "delivery_summary": _normalize_text(delivery_summary, max_length=600),
        "acceptance_criteria": _normalized_list(acceptance_criteria),
        "verification": {
            "source_signature": source_signature,
            "gate": gate,
            "proof_refs": _normalized_list(proof_refs, safe=True),
        },
        "artifact_refs": _normalized_list(artifact_refs, safe=True),
        "limitations": _normalized_list(limitations),
        "missing_fields": [],
    }
    raw["missing_fields"] = _expected_missing(raw)
    raw["status"] = "ready" if not raw["missing_fields"] else "incomplete"
    raw["review_package_id"] = compute_work_review_package_id(raw)
    return validate_work_review_package(raw)
