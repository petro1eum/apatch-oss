"""Fail-closed TrustChain Cowork execution proposals.

A proposal is not a task, session, or mutation. Network transport lives in
``governed_work_delivery``; this module validates the Platform envelope and
stores only an explicit local acceptance reference.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from apatch import governed_work as G

SCHEMA = "trustchain.apatch-studio.execution-intent.v1"
ACCEPTANCE_SCHEMA = "apatch.execution-proposal-acceptance.v1"
PURPOSE = "apatch_studio.execution_intent.issue"
TTL_SECONDS = 300
_INTENT_RE = re.compile(r"^tcapsei_[0-9a-f]{32}$")
_GROUP_RE = re.compile(r"^tcpg_[0-9a-f]{32}$")
_ITEM_RE = re.compile(r"^tcpwi_[0-9a-f]{32}$")
_PROGRAM_RE = re.compile(r"^tcwp_[0-9a-f]{32}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^ed25519:sha256:[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$")
_KEYS = frozenset({"schema","intent_id","tenant_id","project_group_id","work_item_id","work_item_hash","authority_version","work_program_id","work_program_hash","mode","objective","acceptance_criteria","audience","nonce","issued_at","expires_at","signature"})
_ACCEPTANCE_KEYS = frozenset({"schema","intent_id","proposal_document_hash","proposal_envelope_hash","tenant_id","project_group_id","work_item_id","work_item_hash","authority_version","work_program_id","work_program_hash","change_id","change_hash","accepted_at","signature"})


def _text(value: Any, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or value != value.strip() or unicodedata.normalize("NFC", value) != value or not minimum <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise G.GovernedWorkError(f"{field} is invalid")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise G.GovernedWorkError(f"{field} must be canonical UTC")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise G.GovernedWorkError(f"{field} is invalid") from exc


def _b64(value: Any, length: int, field: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise G.GovernedWorkError(f"{field} is invalid")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except Exception as exc:
        raise G.GovernedWorkError(f"{field} is invalid") from exc
    if len(raw) != length or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise G.GovernedWorkError(f"{field} is invalid")
    return raw


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def validate_execution_proposal(document: Mapping[str, Any], *, trusted_keys: Mapping[str, str | bytes], now: Optional[datetime] = None) -> dict[str, Any]:
    proposal = G._expect_exact_keys(dict(document), _KEYS, "execution proposal")
    if proposal["schema"] != SCHEMA or not _INTENT_RE.fullmatch(str(proposal["intent_id"])):
        raise G.GovernedWorkError("invalid execution proposal identity")
    try:
        if str(uuid.UUID(str(proposal["tenant_id"]))) != proposal["tenant_id"]:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise G.GovernedWorkError("invalid execution proposal tenant_id") from exc
    for field, pattern in (("project_group_id",_GROUP_RE),("work_item_id",_ITEM_RE),("work_item_hash",_HASH_RE),("work_program_id",_PROGRAM_RE),("work_program_hash",_HASH_RE)):
        if not pattern.fullmatch(str(proposal[field])):
            raise G.GovernedWorkError(f"invalid execution proposal {field}")
    version = proposal["authority_version"]
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 2_147_483_647:
        raise G.GovernedWorkError("invalid execution proposal authority_version")
    if proposal["mode"] != "new_change" or proposal["audience"] != "apatch-studio":
        raise G.GovernedWorkError("invalid execution proposal mode or audience")
    _text(proposal["objective"], "objective", 3, 2000)
    criteria = proposal["acceptance_criteria"]
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 12:
        raise G.GovernedWorkError("acceptance_criteria is invalid")
    for item in criteria:
        _text(item, "acceptance_criteria", 3, 500)
    _b64(proposal["nonce"], 32, "nonce")
    issued, expires = _timestamp(proposal["issued_at"], "issued_at"), _timestamp(proposal["expires_at"], "expires_at")
    if not 0 < (expires - issued).total_seconds() <= TTL_SECONDS:
        raise G.GovernedWorkError("execution proposal lifetime is invalid")
    signature = G._expect_exact_keys(proposal["signature"], {"algorithm","key_id","value"}, "signature")
    key_id = str(signature.get("key_id"))
    if signature.get("algorithm") != "Ed25519" or not _KEY_RE.fullmatch(key_id):
        raise G.GovernedWorkError("execution proposal signature is invalid")
    public = trusted_keys.get(key_id)
    if public is None:
        raise G.GovernedWorkError("execution proposal signing key is untrusted")
    raw_public = G._public_key_bytes(public)
    if key_id != "ed25519:sha256:" + hashlib.sha256(raw_public).hexdigest():
        raise G.GovernedWorkError("execution proposal signing key id is invalid")
    G.verify_platform_document_signature(proposal, trusted_keys, purpose=PURPOSE)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current < issued - timedelta(seconds=5) or current >= expires:
        raise G.GovernedWorkError("execution proposal is not currently valid")
    return proposal


def parse_execution_proposal(raw: bytes, *, trusted_keys: Mapping[str, str | bytes], now: Optional[datetime] = None) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > 16 * 1024:
        raise G.GovernedWorkError("execution proposal body size is invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise G.GovernedWorkError("execution proposal must be strict JSON") from exc
    if G.canonical_bytes(value) != raw:
        raise G.GovernedWorkError("execution proposal must be canonical JSON")
    return validate_execution_proposal(value, trusted_keys=trusted_keys, now=now)


def acceptance_path(target_dir: str, change_id: str) -> Path:
    return G.governed_work_root(target_dir) / "proposal_acceptances" / f"{change_id}.json"


def store_acceptance(target_dir: str, *, proposal: Mapping[str, Any], change: Mapping[str, Any], accepted_at: str, key_provider: Any) -> dict[str, Any]:
    body = {"schema":ACCEPTANCE_SCHEMA,"intent_id":proposal["intent_id"],"proposal_document_hash":G.document_hash(proposal),"proposal_envelope_hash":G.envelope_hash(proposal),"tenant_id":proposal["tenant_id"],"project_group_id":proposal["project_group_id"],"work_item_id":proposal["work_item_id"],"work_item_hash":proposal["work_item_hash"],"authority_version":proposal["authority_version"],"work_program_id":proposal["work_program_id"],"work_program_hash":proposal["work_program_hash"],"change_id":change["change_id"],"change_hash":G.document_hash(change),"accepted_at":accepted_at}
    path = acceptance_path(target_dir, change["change_id"])
    if path.exists():
        existing = G._read_document(path)
        G._expect_exact_keys(existing, _ACCEPTANCE_KEYS, "proposal acceptance")
        for key, value in body.items():
            if key != "accepted_at" and existing.get(key) != value:
                raise G.GovernedWorkError("stored proposal acceptance conflicts")
        return {"stored": False, "acceptance": existing}
    signed = G.sign_document(body, key_provider)
    G._expect_exact_keys(signed, _ACCEPTANCE_KEYS, "proposal acceptance")
    G._immutable_write(path, signed)
    return {"stored": True, "acceptance": signed}
