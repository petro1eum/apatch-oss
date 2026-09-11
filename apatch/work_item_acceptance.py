"""Canonical APatch-side validation of Cowork Work Item execution bindings."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from apatch import governed_work as G

ACCEPTANCE_SCHEMA = "apatch.execution-proposal-acceptance.v1"
BINDING_SCHEMA = "trustchain.apatch-studio.work-item-execution-binding.v1"
BINDING_PURPOSE = "apatch_studio.work_item_execution.bind"

_ACCEPTANCE_KEYS = frozenset({"schema","intent_id","proposal_document_hash","proposal_envelope_hash","tenant_id","project_group_id","work_item_id","work_item_hash","authority_version","work_program_id","work_program_hash","change_id","change_hash","accepted_at","signature"})
_BINDING_KEYS = frozenset({"schema","binding_id","tenant_id","project_group_id","work_item_id","accepted_work_item_hash","accepted_authority_version","current_work_item_hash","current_authority_version","work_program_id","work_program_hash","intent_id","intent_document_hash","intent_envelope_hash","proposal_acceptance_hash","change_id","change_hash","actor_ref","binding_authority_version","accepted_at","signature"})
_PATTERNS = {
    "intent_id": re.compile(r"^tcapsei_[0-9a-f]{32}$"),
    "project_group_id": re.compile(r"^tcpg_[0-9a-f]{32}$"),
    "work_item_id": re.compile(r"^tcpwi_[0-9a-f]{32}$"),
    "work_program_id": re.compile(r"^tcwp_[0-9a-f]{32}$"),
    "change_id": re.compile(r"^apchg_[0-9a-f]{32}$"),
    "binding_id": re.compile(r"^tcawieb_[0-9a-f]{32}$"),
}
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_MEMBER_REF = re.compile(r"^member:[0-9a-f]{32}$")


def _uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise G.GovernedWorkError(f"{field} is invalid")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise G.GovernedWorkError(f"{field} is invalid") from exc
    return value


def _pattern(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _PATTERNS[field].fullmatch(value):
        raise G.GovernedWorkError(f"{field} is invalid")
    return value


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise G.GovernedWorkError(f"{field} is invalid")
    return value


def validate_acceptance(document: Mapping[str, Any]) -> dict[str, Any]:
    value = G._expect_exact_keys(dict(document), _ACCEPTANCE_KEYS, "proposal acceptance")
    if value["schema"] != ACCEPTANCE_SCHEMA:
        raise G.GovernedWorkError("proposal acceptance schema is invalid")
    _pattern(value["intent_id"], "intent_id")
    _uuid(value["tenant_id"], "tenant_id")
    for field in ("project_group_id", "work_item_id", "work_program_id", "change_id"):
        _pattern(value[field], field)
    for field in ("proposal_document_hash", "proposal_envelope_hash", "work_item_hash", "work_program_hash", "change_hash"):
        _hash(value[field], field)
    G._integer(value["authority_version"], "authority_version", minimum=1)
    G._timestamp(value["accepted_at"], "accepted_at")
    G._expect_exact_keys(value["signature"], {"algorithm", "key_id", "value"}, "signature")
    G.assert_privacy_safe(value)
    return value


def validate_binding(document: Mapping[str, Any], *, trusted_keys: Mapping[str, str | bytes]) -> dict[str, Any]:
    value = G._expect_exact_keys(dict(document), _BINDING_KEYS, "work item execution binding")
    if value["schema"] != BINDING_SCHEMA:
        raise G.GovernedWorkError("work item execution binding schema is invalid")
    _pattern(value["binding_id"], "binding_id")
    _uuid(value["tenant_id"], "tenant_id")
    for field in ("project_group_id", "work_item_id", "work_program_id", "intent_id", "change_id"):
        _pattern(value[field], field)
    for field in ("accepted_work_item_hash", "current_work_item_hash", "work_program_hash", "intent_document_hash", "intent_envelope_hash", "proposal_acceptance_hash", "change_hash"):
        _hash(value[field], field)
    accepted_version = G._integer(value["accepted_authority_version"], "accepted_authority_version", minimum=1)
    current_version = G._integer(value["current_authority_version"], "current_authority_version", minimum=1)
    if current_version not in {accepted_version, accepted_version + 1}:
        raise G.GovernedWorkError("current authority version is invalid")
    G._integer(value["binding_authority_version"], "binding_authority_version", minimum=1)
    actor_ref = G._required_text(value["actor_ref"], "actor_ref")
    if not _MEMBER_REF.fullmatch(actor_ref):
        raise G.GovernedWorkError("actor_ref must be a canonical opaque member subject")
    G._timestamp(value["accepted_at"], "accepted_at")
    G._expect_exact_keys(value["signature"], {"algorithm", "key_id", "value"}, "signature")
    G.assert_privacy_safe(value)
    G.verify_platform_document_signature(value, trusted_keys, purpose=BINDING_PURPOSE)
    return value


def binding_path(target_dir: str, binding_id: str) -> Path:
    return G.governed_work_root(target_dir) / "work_item_execution_bindings" / f"{binding_id}.json"


def store_binding(target_dir: str, *, binding: Mapping[str, Any], acceptance: Mapping[str, Any], change: Mapping[str, Any], trusted_keys: Mapping[str, str | bytes]) -> dict[str, Any]:
    exact = validate_binding(binding, trusted_keys=trusted_keys)
    accepted = validate_acceptance(acceptance)
    exact_change = G.validate_change(change)
    expected = {
        "tenant_id": accepted["tenant_id"], "project_group_id": accepted["project_group_id"],
        "work_item_id": accepted["work_item_id"], "accepted_work_item_hash": accepted["work_item_hash"],
        "accepted_authority_version": accepted["authority_version"], "work_program_id": accepted["work_program_id"],
        "work_program_hash": accepted["work_program_hash"], "intent_id": accepted["intent_id"],
        "intent_document_hash": accepted["proposal_document_hash"], "intent_envelope_hash": accepted["proposal_envelope_hash"],
        "proposal_acceptance_hash": G.document_hash(accepted), "change_id": exact_change["change_id"],
        "change_hash": G.document_hash(exact_change), "accepted_at": accepted["accepted_at"],
    }
    if any(exact[field] != expected_value for field, expected_value in expected.items()):
        raise G.GovernedWorkError("work item execution binding does not match its acceptance and Change")
    path = binding_path(target_dir, exact["binding_id"])
    if path.exists():
        existing = validate_binding(G._read_document(path), trusted_keys=trusted_keys)
        if G.canonical_bytes(existing) != G.canonical_bytes(exact):
            raise G.GovernedWorkError("stored work item execution binding conflicts")
        stored = False
    else:
        G._immutable_write(path, exact)
        stored = True
    return {"stored": stored, "binding": exact, "binding_id": exact["binding_id"], "binding_hash": G.document_hash(exact)}
