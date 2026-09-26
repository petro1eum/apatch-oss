"""Shared Avatar evidence contract: facts -> estimates, never facts -> money.

The bundle is the only L2 payload HC may consume when updating an individual's
market prior.  It deliberately keeps three epistemic levels separate:

* a ``WorkEpisode`` is an observed, signed fact about one governed work unit;
* a ``CapabilityEstimate`` is a fallible inference over eligible episodes;
* valuation is not represented here at all and remains an HC Layer-3 concern.

The contract is strict and content-addressed.  Unknown fields are rejected,
attested/verified bundles require an Ed25519 envelope, and every estimate must
cite eligible episodes from the same subject avatar.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from apatch._vendor.avatar_contract.work_review import (
    WorkReviewPackageError,
    validate_work_review_package,
)


CAPABILITY_EVIDENCE_SCHEMA_VERSION = 2
CAPABILITY_EVIDENCE_KIND = "capability_evidence"
CAPABILITY_EVIDENCE_SOURCES = ("apatch",)
CAPABILITY_EVIDENCE_TRUST_LEVELS = ("claimed", "attested", "verified")

EPISODE_OUTCOMES = ("passed", "failed", "unknown")
EPISODE_OUTCOME_BASES = (
    "technical_gate",
    "external_acceptance",
    "work_asset_reuse",
    "owner_claim",
    "missing",
)
EPISODE_ATTRIBUTIONS = (
    "direct_human",
    "agent_piloted",
    "agent_autonomous",
    "mixed",
    "unknown",
)
CAPABILITY_ESTIMATE_STATUSES = ("estimated", "insufficient_evidence")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:@#+/-]+$")
_DIGEST_40 = re.compile(r"^[0-9a-f]{40}$")
_ECONOMIC_KEYS: Set[str] = {
    "amount",
    "bonus",
    "clearing",
    "creator_bonus",
    "currency",
    "escrow",
    "gpi",
    "market_value",
    "marketplace",
    "market_price",
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

_TOP_LEVEL_KEYS = {
    "schema_version",
    "kind",
    "bundle_id",
    "avatar_id",
    "source",
    "trust_level",
    "generated_at",
    "evidence_scope",
    "episodes",
    "capability_estimates",
    "exclusions",
    "signature",
}
_EPISODE_KEYS = {
    "episode_id",
    "avatar_id",
    "project_id",
    "session_id",
    "task",
    "outcome",
    "attribution",
    "evidence_quality",
    "trust_level",
    "occurred_at",
    "eligible_for_capability",
    "exclusion_reasons",
    "proof_ref",
}
_EPISODE_OPTIONAL_KEYS = {"review_package"}
_CAPABILITY_KEYS = {
    "estimate_id",
    "subject_avatar_id",
    "taxonomy_refs",
    "evidence_episode_ids",
    "status",
    "observations",
    "success_estimate",
    "recency",
    "cross_context",
    "attribution_mix",
    "uncertainty",
}


class CapabilityEvidenceError(ValueError):
    """Raised when an Avatar evidence payload violates the shared contract."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _without_signature(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in raw.items() if key != "signature"}


def _bundle_identity_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key not in {"bundle_id", "signature"}
    }


def compute_capability_evidence_bundle_id(raw: Dict[str, Any]) -> str:
    """Content-address a bundle before its signature envelope is added."""
    return hashlib.sha256(
        _canonical(_bundle_identity_payload(raw)).encode("utf-8")
    ).hexdigest()[:40]


def capability_evidence_signing_bytes(raw: Dict[str, Any]) -> bytes:
    """Canonical bytes covered by the bundle signature (includes bundle_id)."""
    return _canonical(_without_signature(raw)).encode("utf-8")


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


def assert_capability_evidence_content_safe(raw: Dict[str, Any]) -> None:
    found = _scan_forbidden(raw)
    if found:
        raise CapabilityEvidenceError(
            f"forbidden fields in CapabilityEvidence: {sorted(found)}"
        )


def _require_iso(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise CapabilityEvidenceError(f"{field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CapabilityEvidenceError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CapabilityEvidenceError(f"{field_name} must include a timezone")


def _require_safe_id(value: Any, field_name: str, *, digest: bool = False) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise CapabilityEvidenceError(f"{field_name} must be 1..256 characters")
    pattern = _DIGEST_40 if digest else _SAFE_ID
    if not pattern.fullmatch(value):
        raise CapabilityEvidenceError(f"{field_name} is not a content-safe identifier")


def _require_string_list(value: Any, field_name: str, *, safe: bool = False) -> List[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CapabilityEvidenceError(f"{field_name} must be an array of strings")
    if len(value) > 5000:
        raise CapabilityEvidenceError(f"{field_name} exceeds the 5000 item limit")
    if safe and any(not _SAFE_ID.fullmatch(item) for item in value):
        raise CapabilityEvidenceError(f"{field_name} must contain content-safe identifiers")
    return value


def _validate_signature_envelope(
    signature: Any,
    *,
    avatar_id: str,
    required: bool,
) -> None:
    if signature is None:
        if required:
            raise CapabilityEvidenceError("attested evidence requires a signature")
        return
    if not isinstance(signature, dict):
        raise CapabilityEvidenceError("signature must be an object")
    if set(signature) != {"algorithm", "key_id", "public_key", "value"}:
        raise CapabilityEvidenceError("signature envelope fields are invalid")
    if signature.get("algorithm") != "ed25519":
        raise CapabilityEvidenceError("signature.algorithm must be ed25519")
    if signature.get("key_id") != avatar_id:
        raise CapabilityEvidenceError("signature.key_id must equal avatar_id")
    try:
        public_key = base64.b64decode(signature.get("public_key") or "", validate=True)
        value = base64.b64decode(signature.get("value") or "", validate=True)
    except Exception as exc:
        raise CapabilityEvidenceError("signature values must be valid base64") from exc
    if len(public_key) != 32 or len(value) != 64:
        raise CapabilityEvidenceError("signature must contain a 32-byte key and 64-byte value")
    expected_key_id = hashlib.sha256(public_key).hexdigest()[:32]
    if expected_key_id != avatar_id:
        raise CapabilityEvidenceError("signature public key does not match avatar_id")


def verify_capability_evidence_signature(raw: Dict[str, Any]) -> bool:
    """Cryptographically verify a bundle using its self-contained public key."""
    signature = raw.get("signature") if isinstance(raw, dict) else None
    try:
        _validate_signature_envelope(
            signature,
            avatar_id=str(raw.get("avatar_id") or ""),
            required=True,
        )
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = base64.b64decode(signature["public_key"], validate=True)
        value = base64.b64decode(signature["value"], validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            value, capability_evidence_signing_bytes(raw)
        )
        return True
    except Exception:
        return False


def _validate_episode(episode: Any, bundle_avatar_id: str) -> None:
    if not isinstance(episode, dict):
        raise CapabilityEvidenceError("episodes must contain objects")
    unknown = set(episode) - (_EPISODE_KEYS | _EPISODE_OPTIONAL_KEYS)
    if unknown:
        raise CapabilityEvidenceError(f"unknown WorkEpisode fields: {sorted(unknown)}")
    missing = _EPISODE_KEYS - set(episode)
    if missing:
        raise CapabilityEvidenceError(f"missing WorkEpisode fields: {sorted(missing)}")
    _require_safe_id(episode["episode_id"], "episode_id", digest=True)
    _require_safe_id(episode["avatar_id"], "episode.avatar_id")
    _require_safe_id(episode["project_id"], "episode.project_id")
    _require_safe_id(episode["session_id"], "episode.session_id")
    if episode["avatar_id"] != bundle_avatar_id:
        raise CapabilityEvidenceError("episode.avatar_id must equal bundle avatar_id")
    if episode["trust_level"] not in CAPABILITY_EVIDENCE_TRUST_LEVELS:
        raise CapabilityEvidenceError("episode.trust_level is invalid")
    _require_iso(episode["occurred_at"], "episode.occurred_at")

    task = episode["task"]
    if not isinstance(task, dict) or set(task) != {"label", "taxonomy_refs", "spec_refs"}:
        raise CapabilityEvidenceError("episode.task fields are invalid")
    label = task.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > 240 or "\n" in label):
        raise CapabilityEvidenceError("episode.task.label must be a single line <= 240 chars")
    _require_string_list(task["taxonomy_refs"], "episode.task.taxonomy_refs", safe=True)
    _require_string_list(task["spec_refs"], "episode.task.spec_refs", safe=True)

    outcome = episode["outcome"]
    if not isinstance(outcome, dict) or set(outcome) != {
        "status", "basis", "observed_at", "reference"
    }:
        raise CapabilityEvidenceError("episode.outcome fields are invalid")
    if outcome["status"] not in EPISODE_OUTCOMES:
        raise CapabilityEvidenceError("episode.outcome.status is invalid")
    if outcome["basis"] not in EPISODE_OUTCOME_BASES:
        raise CapabilityEvidenceError("episode.outcome.basis is invalid")
    if outcome["observed_at"] is not None:
        _require_iso(outcome["observed_at"], "episode.outcome.observed_at")
    reference = outcome["reference"]
    if reference is not None:
        _require_safe_id(reference, "episode.outcome.reference")

    attribution = episode["attribution"]
    if not isinstance(attribution, dict) or set(attribution) != {
        "actor_key_id", "principal_key_id", "mode", "resolved", "basis"
    }:
        raise CapabilityEvidenceError("episode.attribution fields are invalid")
    _require_safe_id(attribution["actor_key_id"], "episode.attribution.actor_key_id")
    if attribution["principal_key_id"] is not None:
        _require_safe_id(
            attribution["principal_key_id"], "episode.attribution.principal_key_id"
        )
    if attribution["mode"] not in EPISODE_ATTRIBUTIONS:
        raise CapabilityEvidenceError("episode.attribution.mode is invalid")
    if type(attribution["resolved"]) is not bool:
        raise CapabilityEvidenceError("episode.attribution.resolved must be boolean")
    if not isinstance(attribution["basis"], str) or len(attribution["basis"]) > 120:
        raise CapabilityEvidenceError("episode.attribution.basis is invalid")

    quality = episode["evidence_quality"]
    expected_quality = {"signature", "gate", "outcome", "rights"}
    if not isinstance(quality, dict) or set(quality) != expected_quality:
        raise CapabilityEvidenceError("episode.evidence_quality fields are invalid")
    allowed_quality = {
        "signature": {"verified", "missing", "invalid", "unverifiable"},
        "gate": {"falsified", "unprobed", "false_gate", "not_applicable"},
        "outcome": {"observed", "proxy", "missing"},
        "rights": {"confirmed", "restricted", "unknown"},
    }
    for name, allowed in allowed_quality.items():
        if quality[name] not in allowed:
            raise CapabilityEvidenceError(f"episode.evidence_quality.{name} is invalid")

    if type(episode["eligible_for_capability"]) is not bool:
        raise CapabilityEvidenceError("eligible_for_capability must be boolean")
    reasons = _require_string_list(
        episode["exclusion_reasons"], "episode.exclusion_reasons", safe=True
    )
    if episode["eligible_for_capability"]:
        if reasons:
            raise CapabilityEvidenceError("eligible episode cannot have exclusion reasons")
        if quality["signature"] != "verified":
            raise CapabilityEvidenceError("eligible episode requires a verified signature")
        if quality["rights"] != "confirmed":
            raise CapabilityEvidenceError("eligible episode requires confirmed rights")
        if quality["gate"] == "false_gate":
            raise CapabilityEvidenceError("false-gate episode cannot be eligible")
        if quality["outcome"] != "observed" or outcome["status"] == "unknown":
            raise CapabilityEvidenceError("eligible episode requires an observed outcome")
        if outcome["basis"] not in {"external_acceptance", "work_asset_reuse"}:
            raise CapabilityEvidenceError(
                "eligible episode requires an independently observed outcome basis"
            )
        if not attribution["resolved"] or attribution["mode"] == "unknown":
            raise CapabilityEvidenceError("eligible episode requires resolved attribution")
    if not isinstance(episode["proof_ref"], dict):
        raise CapabilityEvidenceError("episode.proof_ref must be an object")
    if "review_package" in episode:
        try:
            review_package = validate_work_review_package(episode["review_package"])
        except WorkReviewPackageError as exc:
            raise CapabilityEvidenceError(
                f"episode.review_package is invalid: {exc}"
            ) from exc
        verification = review_package["verification"]
        if verification["source_signature"] != quality["signature"]:
            raise CapabilityEvidenceError(
                "review package signature state must match episode evidence quality"
            )
        if verification["gate"] != quality["gate"]:
            raise CapabilityEvidenceError(
                "review package gate must match episode evidence quality"
            )
        proof_ids = sorted(
            str(value)
            for value in episode["proof_ref"].get("op_ids") or []
            if value
        )
        if verification["proof_refs"] != proof_ids:
            raise CapabilityEvidenceError(
                "review package proof refs must match episode proof_ref"
            )
        if not set(task["spec_refs"]) <= set(review_package["artifact_refs"]):
            raise CapabilityEvidenceError(
                "review package must retain every episode spec reference"
            )


def _validate_capability(
    capability: Any,
    *,
    bundle_avatar_id: str,
    eligible_episode_ids: Set[str],
) -> None:
    if not isinstance(capability, dict):
        raise CapabilityEvidenceError("capability_estimates must contain objects")
    unknown = set(capability) - _CAPABILITY_KEYS
    if unknown:
        raise CapabilityEvidenceError(f"unknown CapabilityEstimate fields: {sorted(unknown)}")
    missing = _CAPABILITY_KEYS - set(capability)
    if missing:
        raise CapabilityEvidenceError(f"missing CapabilityEstimate fields: {sorted(missing)}")
    _require_safe_id(capability["estimate_id"], "estimate_id", digest=True)
    _require_safe_id(capability["subject_avatar_id"], "subject_avatar_id")
    if capability["subject_avatar_id"] != bundle_avatar_id:
        raise CapabilityEvidenceError("CapabilityEstimate subject must equal bundle avatar_id")
    taxonomy_refs = _require_string_list(
        capability["taxonomy_refs"], "capability.taxonomy_refs", safe=True
    )
    if not taxonomy_refs:
        raise CapabilityEvidenceError("CapabilityEstimate requires taxonomy_refs")
    evidence_ids = _require_string_list(
        capability["evidence_episode_ids"],
        "capability.evidence_episode_ids",
        safe=True,
    )
    if not evidence_ids or not set(evidence_ids) <= eligible_episode_ids:
        raise CapabilityEvidenceError("CapabilityEstimate cites ineligible episodes")
    if capability["status"] not in CAPABILITY_ESTIMATE_STATUSES:
        raise CapabilityEvidenceError("CapabilityEstimate status is invalid")

    observations = capability["observations"]
    if not isinstance(observations, dict) or set(observations) != {
        "attempted", "passed", "failed"
    }:
        raise CapabilityEvidenceError("capability.observations fields are invalid")
    if any(type(observations[name]) is not int or observations[name] < 0 for name in observations):
        raise CapabilityEvidenceError("capability observations must be non-negative integers")
    if observations["attempted"] != observations["passed"] + observations["failed"]:
        raise CapabilityEvidenceError("capability attempted must equal passed + failed")
    if observations["attempted"] != len(evidence_ids):
        raise CapabilityEvidenceError("capability attempted must equal evidence count")

    success = capability["success_estimate"]
    if not isinstance(success, dict) or set(success) != {"mean", "lower", "upper", "method"}:
        raise CapabilityEvidenceError("capability.success_estimate fields are invalid")
    numeric = (success["mean"], success["lower"], success["upper"])
    if any(value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)) for value in numeric):
        raise CapabilityEvidenceError("success estimate values must be numbers or null")
    if any(value is not None and not 0.0 <= float(value) <= 1.0 for value in numeric):
        raise CapabilityEvidenceError("success estimate values must be between 0 and 1")
    if capability["status"] == "estimated":
        if observations["attempted"] < 2 or any(value is None for value in numeric):
            raise CapabilityEvidenceError("estimated capability requires >=2 observations and an interval")
        if not float(success["lower"]) <= float(success["mean"]) <= float(success["upper"]):
            raise CapabilityEvidenceError("success interval ordering is invalid")
    if not isinstance(success["method"], str) or len(success["method"]) > 80:
        raise CapabilityEvidenceError("success_estimate.method is invalid")

    recency = capability["recency"]
    if not isinstance(recency, dict) or set(recency) != {"last_observed_at", "state"}:
        raise CapabilityEvidenceError("capability.recency fields are invalid")
    _require_iso(recency["last_observed_at"], "capability.recency.last_observed_at")
    if recency["state"] not in {"fresh", "aging", "stale"}:
        raise CapabilityEvidenceError("capability.recency.state is invalid")

    cross_context = capability["cross_context"]
    if not isinstance(cross_context, dict) or set(cross_context) != {
        "project_count", "observed_across_projects"
    }:
        raise CapabilityEvidenceError("capability.cross_context fields are invalid")
    if type(cross_context["project_count"]) is not int or cross_context["project_count"] < 1:
        raise CapabilityEvidenceError("cross_context.project_count must be positive")
    if type(cross_context["observed_across_projects"]) is not bool:
        raise CapabilityEvidenceError("observed_across_projects must be boolean")

    attribution_mix = capability["attribution_mix"]
    if not isinstance(attribution_mix, dict) or set(attribution_mix) - set(EPISODE_ATTRIBUTIONS):
        raise CapabilityEvidenceError("capability.attribution_mix is invalid")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 for value in attribution_mix.values()):
        raise CapabilityEvidenceError("attribution_mix values must be non-negative")

    uncertainty = capability["uncertainty"]
    if not isinstance(uncertainty, dict) or set(uncertainty) != {"level", "reasons"}:
        raise CapabilityEvidenceError("capability.uncertainty fields are invalid")
    if uncertainty["level"] not in {"low", "medium", "high"}:
        raise CapabilityEvidenceError("capability.uncertainty.level is invalid")
    _require_string_list(uncertainty["reasons"], "capability.uncertainty.reasons", safe=True)


@dataclass
class CapabilityEvidenceBundle:
    schema_version: int
    kind: str
    bundle_id: str
    avatar_id: str
    source: str
    trust_level: str
    generated_at: str
    evidence_scope: Dict[str, Any]
    episodes: List[Dict[str, Any]] = field(default_factory=list)
    capability_estimates: List[Dict[str, Any]] = field(default_factory=list)
    exclusions: Dict[str, Any] = field(default_factory=dict)
    signature: Optional[Dict[str, str]] = None

    @classmethod
    def from_wire(
        cls,
        raw: Dict[str, Any],
        *,
        verify_signature: bool = False,
    ) -> "CapabilityEvidenceBundle":
        if not isinstance(raw, dict):
            raise CapabilityEvidenceError("CapabilityEvidence root must be an object")
        assert_capability_evidence_content_safe(raw)
        unknown = set(raw) - _TOP_LEVEL_KEYS
        if unknown:
            raise CapabilityEvidenceError(f"unknown CapabilityEvidence fields: {sorted(unknown)}")
        missing = (_TOP_LEVEL_KEYS - {"signature"}) - set(raw)
        if missing:
            raise CapabilityEvidenceError(f"missing CapabilityEvidence fields: {sorted(missing)}")
        bundle = cls(
            schema_version=raw["schema_version"],
            kind=raw["kind"],
            bundle_id=raw["bundle_id"],
            avatar_id=raw["avatar_id"],
            source=raw["source"],
            trust_level=raw["trust_level"],
            generated_at=raw["generated_at"],
            evidence_scope=dict(raw["evidence_scope"]),
            episodes=list(raw["episodes"]),
            capability_estimates=list(raw["capability_estimates"]),
            exclusions=dict(raw["exclusions"]),
            signature=dict(raw["signature"]) if isinstance(raw.get("signature"), dict) else None,
        )
        bundle.validate(verify_signature=verify_signature)
        return bundle

    def to_wire(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    def validate(self, *, verify_signature: bool = False) -> None:
        raw = self.to_wire()
        assert_capability_evidence_content_safe(raw)
        if self.schema_version != CAPABILITY_EVIDENCE_SCHEMA_VERSION:
            raise CapabilityEvidenceError(
                f"schema_version must be {CAPABILITY_EVIDENCE_SCHEMA_VERSION}"
            )
        if self.kind != CAPABILITY_EVIDENCE_KIND:
            raise CapabilityEvidenceError(f"kind must be {CAPABILITY_EVIDENCE_KIND!r}")
        if self.source not in CAPABILITY_EVIDENCE_SOURCES:
            raise CapabilityEvidenceError("CapabilityEvidence source is invalid")
        if self.trust_level not in CAPABILITY_EVIDENCE_TRUST_LEVELS:
            raise CapabilityEvidenceError("CapabilityEvidence trust_level is invalid")
        _require_safe_id(self.bundle_id, "bundle_id", digest=True)
        _require_safe_id(self.avatar_id, "avatar_id")
        _require_iso(self.generated_at, "generated_at")
        if compute_capability_evidence_bundle_id(raw) != self.bundle_id:
            raise CapabilityEvidenceError("bundle_id does not match bundle content")

        if not isinstance(self.evidence_scope, dict) or set(self.evidence_scope) != {
            "event_count", "project_count", "from", "to"
        }:
            raise CapabilityEvidenceError("evidence_scope fields are invalid")
        for name in ("event_count", "project_count"):
            if type(self.evidence_scope[name]) is not int or self.evidence_scope[name] < 0:
                raise CapabilityEvidenceError(f"evidence_scope.{name} must be non-negative")
        for name in ("from", "to"):
            if self.evidence_scope[name] is not None:
                _require_iso(self.evidence_scope[name], f"evidence_scope.{name}")

        if not isinstance(self.episodes, list) or not isinstance(self.capability_estimates, list):
            raise CapabilityEvidenceError("episodes and capability_estimates must be arrays")
        for episode in self.episodes:
            _validate_episode(episode, self.avatar_id)
        episode_ids = [episode["episode_id"] for episode in self.episodes]
        if len(episode_ids) != len(set(episode_ids)):
            raise CapabilityEvidenceError("duplicate episode_id")
        eligible_ids = {
            episode["episode_id"]
            for episode in self.episodes
            if episode["eligible_for_capability"]
        }
        for capability in self.capability_estimates:
            _validate_capability(
                capability,
                bundle_avatar_id=self.avatar_id,
                eligible_episode_ids=eligible_ids,
            )
        estimate_ids = [item["estimate_id"] for item in self.capability_estimates]
        if len(estimate_ids) != len(set(estimate_ids)):
            raise CapabilityEvidenceError("duplicate estimate_id")

        if not isinstance(self.exclusions, dict) or set(self.exclusions) != {"count", "reasons"}:
            raise CapabilityEvidenceError("exclusions fields are invalid")
        if type(self.exclusions["count"]) is not int or self.exclusions["count"] < 0:
            raise CapabilityEvidenceError("exclusions.count must be non-negative")
        reasons = self.exclusions["reasons"]
        if not isinstance(reasons, dict) or any(
            not isinstance(key, str) or type(value) is not int or value < 0
            for key, value in reasons.items()
        ):
            raise CapabilityEvidenceError("exclusions.reasons must be a count map")
        if self.exclusions["count"] != sum(reasons.values()):
            raise CapabilityEvidenceError("exclusions.count must equal reason counts")

        _validate_signature_envelope(
            self.signature,
            avatar_id=self.avatar_id,
            required=self.trust_level in {"attested", "verified"},
        )
        if verify_signature and not verify_capability_evidence_signature(raw):
            raise CapabilityEvidenceError("CapabilityEvidence signature verification failed")
