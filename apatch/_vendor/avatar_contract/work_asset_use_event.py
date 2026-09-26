"""Content-safe WorkAsset reuse event shared by apatch and HC Tracker."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

WORK_ASSET_USE_SCHEMA_VERSION = 1
WORK_ASSET_USE_KIND = "work_asset_use"
WORK_ASSET_USE_SOURCES = ("apatch",)
WORK_ASSET_USE_ADAPTATIONS = ("unchanged", "adapted")
WORK_ASSET_USE_TRUST_LEVELS = ("claimed", "attested", "verified")

_FORBIDDEN_KEYS: Set[str] = {
    "amount",
    "bonus",
    "clearing",
    "command",
    "credential",
    "creator_bonus",
    "currency",
    "customer_data",
    "escrow",
    "gpi",
    "marketplace",
    "market_price",
    "path",
    "pi",
    "portability_index",
    "price",
    "private_prompt",
    "prompt",
    "raw_log",
    "raw_work",
    "scarcity_index",
    "secret",
    "source_code",
}

_ALLOWED_KEYS = {
    "schema_version",
    "kind",
    "event_id",
    "idempotency_key",
    "avatar_id",
    "source",
    "trust_level",
    "spec_id",
    "project_id",
    "adaptation",
    "verification_ok",
    "occurred_at",
    "content_safe",
    "session_id",
    "asset_id",
    "spec_refs",
    "ledger_anchor",
}
_LEDGER_ANCHOR_KEYS = {"op_id", "signature", "payload_sha256"}
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:@#+-]+$")
_EVENT_ID = re.compile(r"^[0-9a-f]{40}$")


class WorkAssetUseEventError(ValueError):
    """Raised when a WorkAsset use event violates its shared contract."""


def _is_safe_identifier(value: str) -> bool:
    return value not in (".", "..") and bool(_SAFE_IDENTIFIER.fullmatch(value))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_work_asset_use_event_id(
    *,
    avatar_id: str,
    project_id: str,
    spec_id: str,
    occurred_at: str,
    adaptation: str,
    session_id: Optional[str] = None,
    asset_id: Optional[str] = None,
) -> str:
    """Content-address the immutable identity of one recorded use attempt."""
    basis = [
        avatar_id,
        project_id,
        spec_id,
        session_id or "",
        asset_id or "",
        occurred_at,
        adaptation,
    ]
    return hashlib.sha256(_canonical(basis).encode("utf-8")).hexdigest()[:40]


def _scan_forbidden_keys(value: Any) -> Set[str]:
    found: Set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in _FORBIDDEN_KEYS:
                found.add(key.lower())
            found |= _scan_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            found |= _scan_forbidden_keys(child)
    return found


def assert_work_asset_use_content_safe(payload: Dict[str, Any]) -> None:
    """Reject economics, raw work, commands, credentials, and private context."""
    found = _scan_forbidden_keys(payload)
    if found:
        raise WorkAssetUseEventError(
            f"forbidden keys in WorkAssetUseEvent: {sorted(found)}"
        )


@dataclass
class WorkAssetUseEvent:
    schema_version: int
    kind: str
    event_id: str
    idempotency_key: str
    avatar_id: str
    source: str
    trust_level: str
    spec_id: str
    project_id: str
    adaptation: str
    verification_ok: bool
    occurred_at: str
    content_safe: bool = True
    session_id: Optional[str] = None
    asset_id: Optional[str] = None
    spec_refs: List[str] = field(default_factory=list)
    ledger_anchor: Optional[Dict[str, Any]] = None

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "WorkAssetUseEvent":
        if not isinstance(raw, dict):
            raise WorkAssetUseEventError("WorkAssetUseEvent root must be an object")
        assert_work_asset_use_content_safe(raw)
        unknown = set(raw) - _ALLOWED_KEYS
        if unknown:
            raise WorkAssetUseEventError(
                f"unknown WorkAssetUseEvent fields: {sorted(unknown)}"
            )
        required = {
            "schema_version",
            "kind",
            "event_id",
            "idempotency_key",
            "avatar_id",
            "source",
            "trust_level",
            "spec_id",
            "project_id",
            "adaptation",
            "verification_ok",
            "occurred_at",
            "content_safe",
        }
        missing = required - set(raw)
        if missing:
            raise WorkAssetUseEventError(
                f"missing WorkAssetUseEvent fields: {sorted(missing)}"
            )
        if type(raw["schema_version"]) is not int:
            raise WorkAssetUseEventError("schema_version must be an integer")
        for name in (
            "kind",
            "event_id",
            "idempotency_key",
            "avatar_id",
            "source",
            "trust_level",
            "spec_id",
            "project_id",
            "adaptation",
            "occurred_at",
        ):
            if not isinstance(raw[name], str):
                raise WorkAssetUseEventError(f"{name} must be a string")
        if type(raw["verification_ok"]) is not bool:
            raise WorkAssetUseEventError("verification_ok must be a boolean")
        if raw["content_safe"] is not True:
            raise WorkAssetUseEventError("content_safe must be true")
        for name in ("session_id", "asset_id"):
            if raw.get(name) is not None and not isinstance(raw[name], str):
                raise WorkAssetUseEventError(f"{name} must be a string")
        spec_refs = raw.get("spec_refs") or []
        if not isinstance(spec_refs, list) or any(
            not isinstance(item, str) for item in spec_refs
        ):
            raise WorkAssetUseEventError("spec_refs must be an array of strings")
        if raw.get("ledger_anchor") is not None and not isinstance(
            raw["ledger_anchor"], dict
        ):
            raise WorkAssetUseEventError("ledger_anchor must be an object")
        try:
            event = cls(
                schema_version=raw["schema_version"],
                kind=raw["kind"],
                event_id=raw["event_id"],
                idempotency_key=raw["idempotency_key"],
                avatar_id=raw["avatar_id"],
                source=raw["source"],
                trust_level=raw["trust_level"],
                spec_id=raw["spec_id"],
                project_id=raw["project_id"],
                adaptation=raw["adaptation"],
                verification_ok=raw["verification_ok"],
                occurred_at=raw["occurred_at"],
                content_safe=raw["content_safe"],
                session_id=raw.get("session_id"),
                asset_id=raw.get("asset_id"),
                spec_refs=list(spec_refs),
                ledger_anchor=(
                    dict(raw["ledger_anchor"])
                    if isinstance(raw.get("ledger_anchor"), dict)
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkAssetUseEventError(f"invalid WorkAssetUseEvent: {exc}") from exc
        return event

    def to_wire(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    def validate(self) -> None:
        if self.schema_version != WORK_ASSET_USE_SCHEMA_VERSION:
            raise WorkAssetUseEventError(
                f"schema_version must be {WORK_ASSET_USE_SCHEMA_VERSION}"
            )
        if self.kind != WORK_ASSET_USE_KIND:
            raise WorkAssetUseEventError(f"kind must be {WORK_ASSET_USE_KIND!r}")
        if self.source not in WORK_ASSET_USE_SOURCES:
            raise WorkAssetUseEventError(
                f"source must be one of {WORK_ASSET_USE_SOURCES}"
            )
        if self.trust_level not in WORK_ASSET_USE_TRUST_LEVELS:
            raise WorkAssetUseEventError(
                f"trust_level must be one of {WORK_ASSET_USE_TRUST_LEVELS}"
            )
        if self.adaptation not in WORK_ASSET_USE_ADAPTATIONS:
            raise WorkAssetUseEventError(
                f"adaptation must be one of {WORK_ASSET_USE_ADAPTATIONS}"
            )
        if not isinstance(self.verification_ok, bool):
            raise WorkAssetUseEventError("verification_ok must be a boolean")
        if self.content_safe is not True:
            raise WorkAssetUseEventError("content_safe must be true")
        for name, value in (
            ("event_id", self.event_id),
            ("idempotency_key", self.idempotency_key),
            ("avatar_id", self.avatar_id),
            ("spec_id", self.spec_id),
            ("project_id", self.project_id),
            ("occurred_at", self.occurred_at),
        ):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise WorkAssetUseEventError(f"{name} must be 1..256 characters")
        if self.event_id != self.idempotency_key:
            raise WorkAssetUseEventError("event_id must equal idempotency_key")
        if not _EVENT_ID.fullmatch(self.event_id):
            raise WorkAssetUseEventError("event_id must be a lowercase 40-char digest")
        for name, value in (
            ("avatar_id", self.avatar_id),
            ("spec_id", self.spec_id),
            ("project_id", self.project_id),
        ):
            if not _is_safe_identifier(value):
                raise WorkAssetUseEventError(f"{name} must be a content-safe identifier")
        try:
            occurred_at = datetime.fromisoformat(
                self.occurred_at.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise WorkAssetUseEventError("occurred_at must be ISO-8601") from exc
        if occurred_at.tzinfo is None:
            raise WorkAssetUseEventError("occurred_at must include a timezone")
        expected_event_id = compute_work_asset_use_event_id(
            avatar_id=self.avatar_id,
            project_id=self.project_id,
            spec_id=self.spec_id,
            session_id=self.session_id,
            asset_id=self.asset_id,
            occurred_at=self.occurred_at,
            adaptation=self.adaptation,
        )
        if self.event_id != expected_event_id:
            raise WorkAssetUseEventError("event_id does not match event content")
        if len(self.spec_refs) > 50:
            raise WorkAssetUseEventError("spec_refs may contain at most 50 items")
        if any(
            not item
            or len(item) > 256
            or not _is_safe_identifier(item)
            for item in self.spec_refs
        ):
            raise WorkAssetUseEventError(
                "spec_refs items must be content-safe identifiers"
            )
        if self.session_id is not None and (
            not isinstance(self.session_id, str)
            or len(self.session_id) > 256
            or not _is_safe_identifier(self.session_id)
        ):
            raise WorkAssetUseEventError("session_id must be a content-safe identifier")
        if self.asset_id is not None and (
            not isinstance(self.asset_id, str)
            or len(self.asset_id) > 256
            or not _is_safe_identifier(self.asset_id)
        ):
            raise WorkAssetUseEventError("asset_id must be a content-safe identifier")
        anchor = self.ledger_anchor
        if self.trust_level in ("attested", "verified") and not isinstance(
            anchor, dict
        ):
            raise WorkAssetUseEventError(
                f"trust_level={self.trust_level} requires ledger_anchor.op_id"
            )
        if anchor is not None:
            if not isinstance(anchor, dict):
                raise WorkAssetUseEventError("ledger_anchor must be an object")
            for key in ("op_id", "signature", "payload_sha256"):
                if not isinstance(anchor.get(key), str) or not anchor[key]:
                    raise WorkAssetUseEventError(
                        f"ledger_anchor.{key} is required"
                    )
            if set(anchor) != _LEDGER_ANCHOR_KEYS:
                raise WorkAssetUseEventError(
                    "ledger_anchor must contain only op_id, signature, payload_sha256"
                )
            if len(anchor["payload_sha256"]) != 64 or any(
                char not in "0123456789abcdef" for char in anchor["payload_sha256"]
            ):
                raise WorkAssetUseEventError(
                    "ledger_anchor.payload_sha256 must be a lowercase SHA-256 digest"
                )
        assert_work_asset_use_content_safe(self.to_wire())
