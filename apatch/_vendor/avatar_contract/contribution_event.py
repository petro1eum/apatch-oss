"""Canonical ContributionEvent contract — Avatar Architecture Canon §7.

Shared schema imported by apatch (emitter, Layer 1) and HC_Platform (consumer,
Layer 3) so both share ONE versioned definition, not "two schemas that match by
convention".

`schema_version` 2 introduces `proof_ref` as the canonical name for the proof
pointer (v1 called it `attestation`) and `kind ∈ {fact, claim}` (v1 emitted
`kind="contribution"`). Version 3 requires a signed, timezone-aware `created_at`
that records when the receipt was built. It is producer evidence, not an
independent trusted timestamp; consumers must not use it as a certificate time
authority without an external inclusion proof.

────────────────────────────────────────────────────────────────────────────
🔴 SIGNING INVARIANT (do not violate)
apatch signs every field except `signature` (`_canonical` over the unsigned dict).
Therefore the proof-pointer key is part of the SIGNED digest:

  * v1 receipts were signed WITH `attestation`   → canonicalize WITH `attestation`.
  * v2/v3 events are signed WITH `proof_ref`     → canonicalize WITH `proof_ref`.

Verification MUST be schema_version-aware and MUST operate on the RAW stored dict
(no key renaming, no normalization, NO legacy `attestation` mirror injected into
v2/v3). Already-issued v1 receipts on disk (~/.trustchain/contributions/<key_id>/*.json)
cannot be re-issued — the key is the identity. The v1 verification path is frozen.

This module's `canonical_unsigned(raw)` is byte-compatible with apatch's `_canonical`
(sort_keys, separators (",",":"), ensure_ascii=False), so it reproduces the v1
digest exactly when given raw v1, v2, or v3 dictionaries — *because it never
transforms the keys.* `from_wire()` normalization is for
CONSUMPTION/aggregation only and must NEVER be used to verify a signature.
────────────────────────────────────────────────────────────────────────────

The historical v1→v2 coordination record remains in SHIM_PR_CHECKLIST.md.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

SCHEMA_VERSION = 3

KINDS = ("fact", "claim")
SOURCES = ("apatch", "witness", "tracker", "manual", "agent")
TRUST_LEVELS = ("claimed", "attested", "verified")

# Legacy aliases tolerated on read (apatch v1 emitter).
_LEGACY_KIND_ALIASES = {"contribution": "fact"}

# Keys that may carry the proof pointer, newest first. Readers accept either.
PROOF_REF_KEYS = ("proof_ref", "attestation")

# Recommended allowlist for event-key validation (e.g. apatch timesheet
# `_ALLOWED_EVENT_KEYS`): keep `attestation` for v1, add `proof_ref` for v2+.
RECOMMENDED_ALLOWED_KEYS: Set[str] = {
    "schema_version", "kind", "event_id", "idempotency_key", "avatar_id",
    "source", "trust_level", "identity", "project", "session", "volume",
    "proof_ref", "attestation", "payload", "cv_delta", "declares_for",
    "methodology_tags", "created_at", "signature",
}

# Economic-layer keys forbidden inside a ContributionEvent (canon §7.3, invariant 1).
_FORBIDDEN_ECON_KEYS: Set[str] = {
    "pi", "gpi", "creator_bonus", "clearing", "marketplace",
    "scarcity_index", "portability_index", "amount", "currency", "bonus",
    "market_price",
}


class ContributionEventError(ValueError):
    """Raised on schema / invariant violation."""


def _canonical(obj: Any) -> str:
    """Stable JSON: sorted keys, compact separators. Byte-compatible with apatch."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_unsigned(raw: Dict[str, Any]) -> str:
    """Canonical signing/verification basis for a RAW stored event dict.

    Drops only `signature`. Does NOT rename or inject keys, so it reproduces the
    exact digest for whichever schema_version produced the dict (v1 carries
    `attestation`, v2+ carries `proof_ref`). This is the ONLY correct input to a
    signature verify — never pass a normalized ContributionEvent through here for
    v1 receipts. See the SIGNING INVARIANT in the module docstring.
    """
    return _canonical({k: v for k, v in raw.items() if k != "signature"})


def proof_ref_of(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Read the proof pointer from a v1, v2, or v3 dict."""
    for k in PROOF_REF_KEYS:
        if raw.get(k) is not None:
            return raw[k]
    return None


def compute_event_id(avatar_id: str, project_id: str, session: Dict[str, Any]) -> str:
    """Deterministic, stable event id == idempotency key (canon §7.3, invariant 3)."""
    basis = _canonical([avatar_id, project_id, session.get("intent"),
                        session.get("started_at"), session.get("ended_at"),
                        sorted(session.get("artifacts") or [])])
    return _sha256_hex(basis.encode("utf-8"))[:32]


@dataclass
class ContributionEvent:
    """The one canonical contract between Layer 1 (work) and Layer 2 (avatar)."""

    schema_version: int
    kind: str
    event_id: str
    idempotency_key: str
    avatar_id: str
    source: str
    trust_level: str
    identity: Dict[str, Any]
    project: Dict[str, Any]
    session: Dict[str, Any]
    volume: Dict[str, int]
    proof_ref: Optional[Dict[str, Any]] = None       # present iff trust_level >= attested
    payload: Optional[Dict[str, Any]] = None          # kind == "fact"
    cv_delta: Optional[Dict[str, float]] = None        # kind == "claim"
    declares_for: Optional[str] = None                 # kind == "claim" -> event_id of the fact
    methodology_tags: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    signature: Optional[str] = None

    # ---- read (CONSUMPTION ONLY — never for signature verification) ---------
    @classmethod
    def from_wire(cls, d: Dict[str, Any]) -> "ContributionEvent":
        """Normalize a v1, v2, or v3 wire dict for aggregation/consumption.

        Absorbs v1 differences: `attestation`->`proof_ref`, `kind="contribution"`->
        `"fact"`, derives `avatar_id` from `identity.key_id`. LOSSY by design — do
        NOT round-trip this back into a signature check; use `canonical_unsigned`
        on the raw dict for that.
        """
        if not isinstance(d, dict):
            raise ContributionEventError("ContributionEvent root must be an object")
        d = dict(d)
        event_id = d.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ContributionEventError("event_id must be a non-empty string")
        schema_version_raw = d.get("schema_version", 1)
        if isinstance(schema_version_raw, bool):
            raise ContributionEventError("schema_version must be an integer")
        try:
            schema_version = int(schema_version_raw)
        except (TypeError, ValueError) as exc:
            raise ContributionEventError("schema_version must be an integer") from exc
        mappings: Dict[str, Dict[str, Any]] = {}
        for name in ("identity", "project", "session", "volume"):
            value = d.get(name)
            if value is None:
                value = {}
            if not isinstance(value, dict):
                raise ContributionEventError(f"{name} must be an object")
            mappings[name] = value
        identity_key_id = mappings["identity"].get("key_id")
        if identity_key_id is not None and not isinstance(identity_key_id, str):
            raise ContributionEventError("identity.key_id must be a string")
        proof_ref = proof_ref_of(d)
        if proof_ref is not None and not isinstance(proof_ref, dict):
            raise ContributionEventError("proof_ref must be an object")
        methodology_tags = d.get("methodology_tags")
        if methodology_tags is None:
            methodology_tags = []
        if not isinstance(methodology_tags, list) or any(
            not isinstance(item, str) for item in methodology_tags
        ):
            raise ContributionEventError("methodology_tags must be an array of strings")
        idempotency_key = d.get("idempotency_key")
        if idempotency_key is None:
            idempotency_key = event_id
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ContributionEventError("idempotency_key must be a non-empty string")
        kind_raw = d.get("kind", "fact")
        if not isinstance(kind_raw, str):
            raise ContributionEventError("kind must be a string")
        kind = _LEGACY_KIND_ALIASES.get(kind_raw, kind_raw)
        for name, default in (("source", "apatch"), ("trust_level", "claimed")):
            value = d.get(name, default)
            if not isinstance(value, str):
                raise ContributionEventError(f"{name} must be a string")
        for name in ("payload", "cv_delta"):
            value = d.get(name)
            if value is not None and not isinstance(value, dict):
                raise ContributionEventError(f"{name} must be an object")
        for name in ("avatar_id", "declares_for", "created_at", "signature"):
            value = d.get(name)
            if value is not None and not isinstance(value, str):
                raise ContributionEventError(f"{name} must be a string")
        avatar_id = d.get("avatar_id") or identity_key_id or ""
        return cls(
            schema_version=schema_version,
            kind=kind,
            event_id=event_id,
            idempotency_key=idempotency_key,
            avatar_id=avatar_id,
            source=d.get("source", "apatch"),
            trust_level=d.get("trust_level", "claimed"),
            identity=mappings["identity"],
            project=mappings["project"],
            session=mappings["session"],
            volume=mappings["volume"],
            proof_ref=proof_ref,
            payload=d.get("payload"),
            cv_delta=d.get("cv_delta"),
            declares_for=d.get("declares_for"),
            methodology_tags=list(methodology_tags),
            created_at=d.get("created_at"),
            signature=d.get("signature"),
        )

    # ---- write (current emission) -------------------------------------------
    def to_wire(self) -> Dict[str, Any]:
        """Serialize an event with no legacy `attestation` mirror.

        Current v3 producers use `proof_ref`; adding a mirror would change the
        signed digest (signing invariant).
        """
        return {k: v for k, v in asdict(self).items() if v is not None}

    def unsigned_dict(self) -> Dict[str, Any]:
        d = self.to_wire()
        d.pop("signature", None)
        return d

    def canonical(self) -> str:
        """Signing basis for this event."""
        return canonical_unsigned(self.to_wire())

    # ---- validation (canon §7.3) -------------------------------------------
    def validate(self) -> None:
        if self.kind not in KINDS:
            raise ContributionEventError(f"kind must be one of {KINDS}, got {self.kind!r}")
        if self.source not in SOURCES:
            raise ContributionEventError(f"source must be one of {SOURCES}, got {self.source!r}")
        if self.trust_level not in TRUST_LEVELS:
            raise ContributionEventError(
                f"trust_level must be one of {TRUST_LEVELS}, got {self.trust_level!r}")
        key_id = (self.identity or {}).get("key_id")
        if key_id and self.avatar_id != key_id:
            raise ContributionEventError("avatar_id must equal identity.key_id")
        if self.trust_level in ("attested", "verified") and not self.proof_ref:
            raise ContributionEventError(f"trust_level={self.trust_level} requires proof_ref")
        if self.kind == "claim" and not self.declares_for:
            raise ContributionEventError("kind=claim requires declares_for")
        if self.schema_version >= 3:
            if not self.created_at:
                raise ContributionEventError(
                    "schema_version>=3 requires signed created_at"
                )
            try:
                created_at = datetime.fromisoformat(
                    self.created_at.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ContributionEventError(
                    "created_at must be an ISO-8601 timestamp"
                ) from exc
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ContributionEventError(
                    "created_at must include a timezone"
                )
        assert_economic_barrier(self.to_wire())


def assert_economic_barrier(payload: Dict[str, Any]) -> None:
    """Raise if any economic-layer key leaked into the event (canon §7.3 #1)."""
    found = _scan_forbidden(payload, _FORBIDDEN_ECON_KEYS)
    if found:
        raise ContributionEventError(
            f"economic-layer keys forbidden in ContributionEvent: {sorted(found)}")


def _scan_forbidden(obj: Any, forbidden: Set[str]) -> Set[str]:
    found: Set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in forbidden:
                found.add(k.lower())
            found |= _scan_forbidden(v, forbidden)
    elif isinstance(obj, list):
        for v in obj:
            found |= _scan_forbidden(v, forbidden)
    return found
