"""Owner-authorized, Tracker-signed taxonomy decision for one WorkEpisode.

The decision classifies *what kind of work an episode represents*.  It is not
evidence that the work succeeded, a skill credential, or a valuation.  HC
Tracker may propose O*NET candidates, but apatch consumes only an explicit
owner decision whose exact payload is signed and whose issuer is pinned.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set


TAXONOMY_DECISION_SCHEMA_VERSION = 1
TAXONOMY_DECISION_KIND = "taxonomy_decision"
TAXONOMY_DECISION_STATUSES = ("accepted", "rejected")
TAXONOMY_DECISION_CHAIN_ID = "hc_avatar_taxonomy"
TAXONOMY_DECISION_EVENT = "TAXONOMY_DECISION_RECORDED"
TAXONOMY_DECISION_AUTHORIZATION_BASIS = "authenticated_avatar_owner"
TAXONOMY_CATALOG_NAME = "O*NET"

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:@#+/-]+$")
_DIGEST_40 = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_64 = re.compile(r"^[0-9a-f]{64}$")
_SOC_CODE = re.compile(r"^\d{2}-\d{4}(?:\.\d{2})?$")
_ALLOWED_REF_PREFIXES = ("soc:", "onet-task:", "onet-skill:", "cbm:")
_TOP_LEVEL_KEYS = {
    "schema_version",
    "kind",
    "decision_id",
    "subject_avatar_id",
    "source_event_id",
    "work_episode_id",
    "proposal",
    "decision",
    "owner",
    "issued_at",
    "trustchain_audit",
}
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
_PRIVATE_CONTENT_KEYS: Set[str] = {
    "command",
    "customer_data",
    "file_content",
    "intent",
    "private_prompt",
    "prompt",
    "raw_intent",
    "raw_log",
    "raw_work",
    "secret",
    "source_code",
}


class TaxonomyDecisionError(ValueError):
    """Raised when a taxonomy decision violates the shared contract."""


def _canonical(value: Any, *, ensure_ascii: bool = False) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ensure_ascii,
    )


def taxonomy_decision_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return the exact object covered by the TrustChain signature."""
    return {key: value for key, value in raw.items() if key != "trustchain_audit"}


def _identity_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key not in {"decision_id", "trustchain_audit"}
    }


def compute_taxonomy_decision_id(raw: Dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical(_identity_payload(raw)).encode("utf-8")
    ).hexdigest()[:40]


def _payload_hash(payload: Dict[str, Any]) -> str:
    safe = json.loads(json.dumps(payload, sort_keys=True, default=str))
    return hashlib.sha256(
        _canonical(safe, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _require_iso(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise TaxonomyDecisionError(f"{field_name} must be ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TaxonomyDecisionError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise TaxonomyDecisionError(f"{field_name} must include a timezone")


def _require_safe_id(
    value: Any,
    field_name: str,
    *,
    digest_length: Optional[int] = None,
) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise TaxonomyDecisionError(f"{field_name} is invalid")
    pattern = _DIGEST_40 if digest_length == 40 else _DIGEST_64 if digest_length == 64 else _SAFE_ID
    if not pattern.fullmatch(value):
        raise TaxonomyDecisionError(f"{field_name} is not a content-safe identifier")
    return value


def _require_label(value: Any, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise TaxonomyDecisionError(f"{field_name} is invalid")
    return value


def _scan_forbidden(value: Any) -> Set[str]:
    found: Set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in _ECONOMIC_KEYS | _PRIVATE_CONTENT_KEYS:
                found.add(key.lower())
            found |= _scan_forbidden(child)
    elif isinstance(value, list):
        for child in value:
            found |= _scan_forbidden(child)
    return found


def assert_taxonomy_decision_content_safe(raw: Dict[str, Any]) -> None:
    found = _scan_forbidden(taxonomy_decision_payload(raw))
    if found:
        raise TaxonomyDecisionError(
            f"forbidden fields in TaxonomyDecision: {sorted(found)}"
        )


def verify_taxonomy_decision_signature(
    raw: Dict[str, Any],
    *,
    trusted_public_key: Optional[str] = None,
) -> bool:
    """Verify exact payload binding and the portable TrustChain Ed25519 proof."""
    try:
        audit = raw["trustchain_audit"]
        payload = taxonomy_decision_payload(raw)
        if not isinstance(audit, dict):
            return False
        if audit.get("chain_id") != TAXONOMY_DECISION_CHAIN_ID:
            return False
        if audit.get("event") != TAXONOMY_DECISION_EVENT:
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
            f"{TAXONOMY_DECISION_CHAIN_ID}:{TAXONOMY_DECISION_EVENT}"
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


def _validate_taxonomy(proposal: Dict[str, Any], status: str) -> None:
    if set(proposal) != {
        "proposal_id",
        "catalog",
        "matcher",
        "intent_sha256",
        "occupation",
        "task",
        "skills",
        "taxonomy_refs",
    }:
        raise TaxonomyDecisionError("proposal fields are invalid")
    _require_safe_id(proposal["proposal_id"], "proposal.proposal_id", digest_length=40)
    _require_safe_id(proposal["intent_sha256"], "proposal.intent_sha256", digest_length=64)

    catalog = proposal["catalog"]
    if not isinstance(catalog, dict) or set(catalog) != {"name", "release"}:
        raise TaxonomyDecisionError("proposal.catalog fields are invalid")
    if catalog["name"] != TAXONOMY_CATALOG_NAME:
        raise TaxonomyDecisionError("proposal.catalog.name is invalid")
    _require_safe_id(catalog["release"], "proposal.catalog.release")

    matcher = proposal["matcher"]
    if not isinstance(matcher, dict) or set(matcher) != {"name", "version"}:
        raise TaxonomyDecisionError("proposal.matcher fields are invalid")
    _require_safe_id(matcher["name"], "proposal.matcher.name")
    _require_safe_id(matcher["version"], "proposal.matcher.version")

    occupation = proposal["occupation"]
    task = proposal["task"]
    skills = proposal["skills"]
    refs = proposal["taxonomy_refs"]
    if status == "accepted" and (not occupation or not task or not refs):
        raise TaxonomyDecisionError(
            "accepted decision requires occupation, task and taxonomy_refs"
        )
    if occupation is not None:
        if not isinstance(occupation, dict) or set(occupation) != {"soc_code", "title"}:
            raise TaxonomyDecisionError("proposal.occupation fields are invalid")
        if not isinstance(occupation["soc_code"], str) or not _SOC_CODE.fullmatch(occupation["soc_code"]):
            raise TaxonomyDecisionError("proposal.occupation.soc_code is invalid")
        _require_label(occupation["title"], "proposal.occupation.title", max_length=240)
    if task is not None:
        if not isinstance(task, dict) or set(task) != {"id", "statement"}:
            raise TaxonomyDecisionError("proposal.task fields are invalid")
        _require_safe_id(str(task["id"]), "proposal.task.id")
        _require_label(task["statement"], "proposal.task.statement", max_length=1000)
    if not isinstance(skills, list) or len(skills) > 25:
        raise TaxonomyDecisionError("proposal.skills must be an array")
    skill_ids: List[str] = []
    for skill in skills:
        if not isinstance(skill, dict) or set(skill) != {"id", "name"}:
            raise TaxonomyDecisionError("proposal.skills fields are invalid")
        skill_ids.append(_require_safe_id(skill["id"], "proposal.skills.id"))
        _require_label(skill["name"], "proposal.skills.name", max_length=240)
    if len(skill_ids) != len(set(skill_ids)):
        raise TaxonomyDecisionError("proposal.skills contains duplicates")

    if not isinstance(refs, list) or len(refs) > 30:
        raise TaxonomyDecisionError("proposal.taxonomy_refs must be an array")
    for ref in refs:
        _require_safe_id(ref, "proposal.taxonomy_refs")
        if not ref.startswith(_ALLOWED_REF_PREFIXES):
            raise TaxonomyDecisionError("proposal.taxonomy_refs prefix is invalid")
    if refs != sorted(set(refs)):
        raise TaxonomyDecisionError("proposal.taxonomy_refs must be sorted and unique")
    if status == "rejected" and refs:
        raise TaxonomyDecisionError("rejected decision cannot accept taxonomy_refs")

    if occupation is not None and f"soc:{occupation['soc_code'].split('.', 1)[0]}" not in refs and status == "accepted":
        raise TaxonomyDecisionError("accepted occupation is not represented in taxonomy_refs")
    if task is not None and f"onet-task:{task['id']}" not in refs and status == "accepted":
        raise TaxonomyDecisionError("accepted task is not represented in taxonomy_refs")
    expected_skill_refs = {f"onet-skill:{value}" for value in skill_ids}
    if status == "accepted" and not expected_skill_refs.issubset(set(refs)):
        raise TaxonomyDecisionError("accepted skills are not represented in taxonomy_refs")


@dataclass(frozen=True)
class TaxonomyDecision:
    schema_version: int
    kind: str
    decision_id: str
    subject_avatar_id: str
    source_event_id: str
    work_episode_id: str
    proposal: Dict[str, Any]
    decision: Dict[str, Any]
    owner: Dict[str, Any]
    issued_at: str
    trustchain_audit: Dict[str, Any]

    @classmethod
    def from_wire(
        cls,
        raw: Dict[str, Any],
        *,
        verify_signature: bool = False,
        trusted_public_key: Optional[str] = None,
    ) -> "TaxonomyDecision":
        if not isinstance(raw, dict):
            raise TaxonomyDecisionError("TaxonomyDecision root must be an object")
        unknown = set(raw) - _TOP_LEVEL_KEYS
        missing = _TOP_LEVEL_KEYS - set(raw)
        if unknown:
            raise TaxonomyDecisionError(f"unknown TaxonomyDecision fields: {sorted(unknown)}")
        if missing:
            raise TaxonomyDecisionError(f"missing TaxonomyDecision fields: {sorted(missing)}")
        assert_taxonomy_decision_content_safe(raw)
        if raw["schema_version"] != TAXONOMY_DECISION_SCHEMA_VERSION:
            raise TaxonomyDecisionError("TaxonomyDecision schema_version is invalid")
        if raw["kind"] != TAXONOMY_DECISION_KIND:
            raise TaxonomyDecisionError("TaxonomyDecision kind is invalid")
        _require_safe_id(raw["decision_id"], "decision_id", digest_length=40)
        if raw["decision_id"] != compute_taxonomy_decision_id(raw):
            raise TaxonomyDecisionError("decision_id does not match content")
        _require_safe_id(raw["subject_avatar_id"], "subject_avatar_id")
        _require_safe_id(raw["source_event_id"], "source_event_id")
        _require_safe_id(raw["work_episode_id"], "work_episode_id", digest_length=40)
        _require_iso(raw["issued_at"], "issued_at")

        decision = raw["decision"]
        if not isinstance(decision, dict) or set(decision) != {
            "status",
            "reason_code",
            "supersedes_decision_id",
        }:
            raise TaxonomyDecisionError("decision fields are invalid")
        if decision["status"] not in TAXONOMY_DECISION_STATUSES:
            raise TaxonomyDecisionError("decision.status is invalid")
        _require_safe_id(decision["reason_code"], "decision.reason_code")
        supersedes = decision["supersedes_decision_id"]
        if supersedes is not None:
            _require_safe_id(
                supersedes,
                "decision.supersedes_decision_id",
                digest_length=40,
            )

        owner = raw["owner"]
        if not isinstance(owner, dict) or set(owner) != {
            "professional_id",
            "authorization_basis",
            "decision_event_id",
        }:
            raise TaxonomyDecisionError("owner fields are invalid")
        _require_safe_id(owner["professional_id"], "owner.professional_id")
        if owner["authorization_basis"] != TAXONOMY_DECISION_AUTHORIZATION_BASIS:
            raise TaxonomyDecisionError("owner.authorization_basis is invalid")
        _require_safe_id(owner["decision_event_id"], "owner.decision_event_id")

        proposal = raw["proposal"]
        if not isinstance(proposal, dict):
            raise TaxonomyDecisionError("proposal must be an object")
        _validate_taxonomy(proposal, decision["status"])

        if not isinstance(raw["trustchain_audit"], dict):
            raise TaxonomyDecisionError("trustchain_audit must be an object")
        if verify_signature and not verify_taxonomy_decision_signature(
            raw,
            trusted_public_key=trusted_public_key,
        ):
            raise TaxonomyDecisionError("TaxonomyDecision signature verification failed")
        return cls(**raw)

    def to_wire(self) -> Dict[str, Any]:
        return asdict(self)


def build_taxonomy_decision_payload(
    *,
    subject_avatar_id: str,
    source_event_id: str,
    work_episode_id: str,
    proposal_id: str,
    catalog_release: str,
    matcher_name: str,
    matcher_version: str,
    intent_sha256: str,
    occupation: Optional[Dict[str, str]],
    task: Optional[Dict[str, str]],
    skills: List[Dict[str, str]],
    taxonomy_refs: List[str],
    status: str,
    reason_code: str,
    supersedes_decision_id: Optional[str],
    owner_professional_id: str,
    owner_decision_event_id: str,
    issued_at: str,
) -> Dict[str, Any]:
    """Build the unsigned payload passed to ``sign_message_payload``."""
    raw: Dict[str, Any] = {
        "schema_version": TAXONOMY_DECISION_SCHEMA_VERSION,
        "kind": TAXONOMY_DECISION_KIND,
        "subject_avatar_id": subject_avatar_id,
        "source_event_id": source_event_id,
        "work_episode_id": work_episode_id,
        "proposal": {
            "proposal_id": proposal_id,
            "catalog": {"name": TAXONOMY_CATALOG_NAME, "release": catalog_release},
            "matcher": {"name": matcher_name, "version": matcher_version},
            "intent_sha256": intent_sha256,
            "occupation": occupation,
            "task": task,
            "skills": sorted(skills, key=lambda item: (item["id"], item["name"])),
            "taxonomy_refs": sorted(set(taxonomy_refs)),
        },
        "decision": {
            "status": status,
            "reason_code": reason_code,
            "supersedes_decision_id": supersedes_decision_id,
        },
        "owner": {
            "professional_id": owner_professional_id,
            "authorization_basis": TAXONOMY_DECISION_AUTHORIZATION_BASIS,
            "decision_event_id": owner_decision_event_id,
        },
        "issued_at": issued_at,
    }
    raw["decision_id"] = compute_taxonomy_decision_id(raw)
    return raw
