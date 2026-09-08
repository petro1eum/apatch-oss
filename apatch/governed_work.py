"""Signed APatch side of TrustChain governed-work bindings (RFP-043).

This module owns only APatch execution facts. TrustChain Platform remains the
authority for ProjectGroup, WorkProgram, WorkRelease, bindings and timesheet
decisions. Documents emitted here are intentionally metadata-only.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


CHANGE_SCHEMA = "apatch.change.v1"
TIMESHEET_SCHEMA = "apatch.timesheet-draft.v1"
EVIDENCE_SCHEMA = "apatch.work-evidence-bundle.v1"
SOURCE_BINDING_SCHEMA = "trustchain.project-source-binding.v1"

PLATFORM_SOURCE_BINDING_PURPOSE = "governed_work.source.bind"
PLATFORM_EVIDENCE_ADMISSION_PURPOSE = "governed_work.evidence.admit"
_PLATFORM_SIGNATURE_DOMAIN = b"TrustChain-Governed-Work\x00v1\x00"

_HASH64_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH16_RE = re.compile(r"^sha256:[0-9a-f]{16}$")
_CHANGE_ID_RE = re.compile(r"^apchg_[0-9a-f]{32}$")
_TIMESHEET_ID_RE = re.compile(r"^apts_[0-9a-f]{32}$")
_BUNDLE_ID_RE = re.compile(r"^apweb_[0-9a-f]{32}$")
_GROUP_ID_RE = re.compile(r"^tcpg_[0-9a-f]{32}$")
_PROGRAM_ID_RE = re.compile(r"^tcwp_[0-9a-f]{32}$")
_BINDING_ID_RE = re.compile(r"^tcpsb_[0-9a-f]{32}$")
_REQUIREMENT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*\d+$")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:/|~[/\\]|[A-Za-z]:[/\\]|file://)")

_SIGNATURE_KEYS = frozenset({"algorithm", "key_id", "value"})
_REQUIREMENT_REF_KEYS = frozenset({"requirement_id", "requirement_hash"})
_CONTRIBUTION_REF_KEYS = frozenset({"event_id", "event_hash"})
_SESSION_REF_KEYS = frozenset(
    {"governed_session_id", "started_at", "ended_at"}
)
_TIMESHEET_REF_KEYS = frozenset(
    {"timesheet_id", "timesheet_hash", "claimed_active_seconds"}
)
_ATTESTATION_REF_KEYS = frozenset(
    {
        "spec_id",
        "requirement_id",
        "attestation_id",
        "attestation_hash",
        "outcome",
    }
)

_CHANGE_KEYS = frozenset(
    {
        "schema",
        "change_id",
        "execution_system",
        "tenant_id",
        "project_group_id",
        "source_kind",
        "work_program_id",
        "work_program_hash",
        "context_release_id",
        "context_release_manifest_hash",
        "spec_id",
        "spec_hash",
        "requirement_refs",
        "purpose_hash",
        "actor_key_id",
        "issued_at",
        "signature",
    }
)
_SOURCE_BINDING_KEYS = frozenset(
    {
        "schema",
        "binding_id",
        "tenant_id",
        "project_group_id",
        "source_kind",
        "work_program_id",
        "work_program_hash",
        "context_release_id",
        "context_release_manifest_hash",
        "execution_system",
        "change_id",
        "change_hash",
        "spec_id",
        "spec_hash",
        "requirement_refs",
        "actor_ref",
        "authority_version",
        "issued_at",
        "signature",
    }
)
_TIMESHEET_KEYS = frozenset(
    {
        "schema",
        "timesheet_id",
        "project_source_binding_id",
        "project_source_binding_hash",
        "subject_key_id",
        "period_started_at",
        "period_ended_at",
        "claimed_active_seconds",
        "session_refs",
        "contribution_event_refs",
        "issued_at",
        "signature",
    }
)
_EVIDENCE_KEYS = frozenset(
    {
        "schema",
        "bundle_id",
        "change_id",
        "change_hash",
        "project_source_binding_id",
        "project_source_binding_hash",
        "spec_id",
        "spec_hash",
        "requirement_refs",
        "attestation_refs",
        "contribution_event_refs",
        "timesheet_ref",
        "issued_at",
        "signature",
    }
)

_FORBIDDEN_KEYS = frozenset(
    {
        "code",
        "source_code",
        "prompt",
        "intent",
        "diff",
        "patch",
        "path",
        "repo_path",
        "host_path",
        "credential",
        "credentials",
        "secret",
        "session_token",
        "private_key",
        "grant",
        "lease",
        "rate",
        "price",
        "salary",
        "invoice",
        "ownership",
        "settlement",
        "professional_status",
    }
)


class GovernedWorkError(ValueError):
    """The governed-work document or transition is invalid."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalise(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GovernedWorkError("NaN and Infinity are forbidden")
        return value
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    if isinstance(value, tuple):
        return [_normalise(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise GovernedWorkError("JSON object keys must be strings")
        return {
            unicodedata.normalize("NFC", key): _normalise(item)
            for key, item in value.items()
        }
    raise GovernedWorkError(f"unsupported canonical JSON type: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            _normalise(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GovernedWorkError(str(exc)) from exc
    return encoded.encode("utf-8")


def strict_json_loads(raw: str | bytes) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GovernedWorkError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=object_pairs)
    except GovernedWorkError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GovernedWorkError("invalid UTF-8 JSON") from exc


def value_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def document_hash(document: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in document.items() if key != "signature"}
    return value_hash(unsigned)


def envelope_hash(document: Mapping[str, Any]) -> str:
    return value_hash(dict(document))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise GovernedWorkError("base64url value is required")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise GovernedWorkError("invalid base64url value") from exc


def signer_key_id(key_provider: Any) -> str:
    try:
        public = key_provider.get_public_key()
    except Exception as exc:
        raise GovernedWorkError("Ed25519 key provider is required") from exc
    if not isinstance(public, bytes) or len(public) != 32:
        raise GovernedWorkError("Ed25519 public key must be 32 raw bytes")
    return hashlib.sha256(public).hexdigest()[:32]


def sign_document(
    document: Mapping[str, Any],
    key_provider: Any,
    *,
    key_id: Optional[str] = None,
) -> Dict[str, Any]:
    unsigned = {key: copy.deepcopy(value) for key, value in document.items() if key != "signature"}
    resolved_key_id = key_id or signer_key_id(key_provider)
    try:
        signature = key_provider.sign(canonical_bytes(unsigned))
    except Exception as exc:
        raise GovernedWorkError("Ed25519 signing failed") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise GovernedWorkError("Ed25519 signature must be 64 raw bytes")
    unsigned["signature"] = {
        "algorithm": "Ed25519",
        "key_id": resolved_key_id,
        "value": _b64url_encode(signature),
    }
    return unsigned


def _platform_signature_payload(
    document: Mapping[str, Any],
    *,
    purpose: str,
) -> bytes:
    if not isinstance(purpose, str) or not purpose.isascii() or not purpose:
        raise GovernedWorkError("Platform signature purpose must be nonempty ASCII")
    unsigned = {
        key: value for key, value in document.items() if key != "signature"
    }
    return (
        _PLATFORM_SIGNATURE_DOMAIN
        + purpose.encode("ascii")
        + b"\x00"
        + canonical_bytes(unsigned)
    )


def _public_key_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = _b64url_decode(value)
        if len(raw) != 32:
            try:
                raw = base64.b64decode(value)
            except Exception:
                pass
    else:
        raise GovernedWorkError("trusted public key must be bytes or base64url")
    if len(raw) != 32:
        raise GovernedWorkError("trusted Ed25519 public key must be 32 bytes")
    return raw


def _verify_signature_payload(
    document: Mapping[str, Any],
    trusted_keys: Mapping[str, str | bytes],
    payload: bytes,
) -> str:
    signature = document.get("signature")
    _expect_exact_keys(signature, _SIGNATURE_KEYS, "signature")
    if signature["algorithm"] != "Ed25519":
        raise GovernedWorkError("signature algorithm must be Ed25519")
    key_id = _required_text(signature["key_id"], "signature.key_id")
    if key_id not in trusted_keys:
        raise GovernedWorkError(f"untrusted signing key: {key_id}")
    raw_signature = _b64url_decode(signature["value"])
    if len(raw_signature) != 64:
        raise GovernedWorkError("Ed25519 signature must decode to 64 bytes")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(
            _public_key_bytes(trusted_keys[key_id])
        ).verify(raw_signature, payload)
    except GovernedWorkError:
        raise
    except Exception as exc:
        raise GovernedWorkError("Ed25519 signature verification failed") from exc
    return key_id


def verify_document_signature(
    document: Mapping[str, Any],
    trusted_keys: Mapping[str, str | bytes],
) -> str:
    unsigned = {
        key: value for key, value in document.items() if key != "signature"
    }
    return _verify_signature_payload(
        document,
        trusted_keys,
        canonical_bytes(unsigned),
    )


def verify_platform_document_signature(
    document: Mapping[str, Any],
    trusted_keys: Mapping[str, str | bytes],
    *,
    purpose: str,
) -> str:
    """Verify TrustChain Platform's purpose-separated governed-work envelope."""
    return _verify_signature_payload(
        document,
        trusted_keys,
        _platform_signature_payload(document, purpose=purpose),
    )


def _expect_exact_keys(value: Any, expected: Iterable[str], label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise GovernedWorkError(f"{label} must be an object")
    actual = set(value)
    expected_set = set(expected)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        unknown = sorted(actual - expected_set)
        raise GovernedWorkError(
            f"{label} keys mismatch; missing={missing}, unknown={unknown}"
        )
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GovernedWorkError(f"{label} must be nonempty text")
    if value != unicodedata.normalize("NFC", value):
        raise GovernedWorkError(f"{label} must be NFC")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GovernedWorkError(f"{label} must be an integer >= {minimum}")
    return value


def _timestamp(value: Any, label: str) -> str:
    text = _required_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernedWorkError(f"{label} must be UTC RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise GovernedWorkError(f"{label} must be UTC RFC3339")
    return text


def _canonical_timestamp(value: Any, label: str) -> str:
    text = _timestamp(value, label)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _matches(value: Any, pattern: re.Pattern[str], label: str) -> str:
    text = _required_text(value, label)
    if not pattern.fullmatch(text):
        raise GovernedWorkError(f"invalid {label}: {text}")
    return text


def _nullable_pair(first: Any, second: Any, label: str) -> None:
    if (first is None) != (second is None):
        raise GovernedWorkError(f"{label} fields must both be null or non-null")
    if first is not None:
        _required_text(first, f"{label}.id")
        _matches(second, _HASH64_RE, f"{label}.hash")


def _validate_signature_shape(document: Mapping[str, Any]) -> None:
    signature = _expect_exact_keys(
        document.get("signature"), _SIGNATURE_KEYS, "signature"
    )
    if signature["algorithm"] != "Ed25519":
        raise GovernedWorkError("signature algorithm must be Ed25519")
    _required_text(signature["key_id"], "signature.key_id")
    if len(_b64url_decode(signature["value"])) != 64:
        raise GovernedWorkError("signature.value must be unpadded base64url Ed25519")


def _validate_requirement_refs(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise GovernedWorkError("requirement_refs must be a nonempty array")
    refs: List[Dict[str, str]] = []
    for index, raw in enumerate(value):
        ref = _expect_exact_keys(
            raw, _REQUIREMENT_REF_KEYS, f"requirement_refs[{index}]"
        )
        refs.append(
            {
                "requirement_id": _matches(
                    ref["requirement_id"], _REQUIREMENT_ID_RE, "requirement_id"
                ),
                "requirement_hash": _matches(
                    ref["requirement_hash"], _HASH16_RE, "requirement_hash"
                ),
            }
        )
    if refs != sorted(refs, key=lambda item: item["requirement_id"]):
        raise GovernedWorkError("requirement_refs must be sorted by requirement_id")
    ids = [item["requirement_id"] for item in refs]
    if len(ids) != len(set(ids)):
        raise GovernedWorkError("requirement_refs must be duplicate-free")
    return refs


def _validate_contribution_refs(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise GovernedWorkError("contribution_event_refs must be a nonempty array")
    refs = []
    for index, raw in enumerate(value):
        ref = _expect_exact_keys(
            raw, _CONTRIBUTION_REF_KEYS, f"contribution_event_refs[{index}]"
        )
        refs.append(
            {
                "event_id": _required_text(ref["event_id"], "event_id"),
                "event_hash": _matches(ref["event_hash"], _HASH64_RE, "event_hash"),
            }
        )
    if refs != sorted(refs, key=lambda item: item["event_id"]):
        raise GovernedWorkError("contribution_event_refs must be sorted by event_id")
    if len({item["event_id"] for item in refs}) != len(refs):
        raise GovernedWorkError("contribution_event_refs must be duplicate-free")
    return refs


def assert_privacy_safe(document: Mapping[str, Any]) -> None:
    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in _FORBIDDEN_KEYS:
                    raise GovernedWorkError(f"forbidden privacy field: {path}{key}")
                visit(item, f"{path}{key}.")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}{index}.")
        elif isinstance(value, str) and _ABSOLUTE_PATH_RE.match(value):
            raise GovernedWorkError(f"filesystem path is forbidden: {path[:-1]}")

    visit(document, "")


def _load_local_provider(target_dir: str, key_provider: Any = None) -> Any:
    if key_provider is not None:
        return key_provider
    from apatch.trust_identity import load_local_identity

    identity = load_local_identity(target_dir)
    provider = getattr(identity, "key_provider", None) if identity else None
    if provider is None:
        raise GovernedWorkError("an enrolled APatch Ed25519 identity is required")
    return provider


def governed_work_root(target_dir: str = ".") -> Path:
    return Path(os.path.abspath(target_dir)) / ".apatch" / "governed_work"


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical_bytes(document))
    os.replace(temporary, path)


def _read_document(path: Path) -> Dict[str, Any]:
    try:
        raw = strict_json_loads(path.read_bytes())
    except OSError as exc:
        raise GovernedWorkError(f"cannot read governed-work document: {path.name}") from exc
    if not isinstance(raw, dict):
        raise GovernedWorkError(f"governed-work document must be an object: {path.name}")
    return raw


def _immutable_write(path: Path, document: Mapping[str, Any]) -> bool:
    if path.exists():
        existing = _read_document(path)
        if canonical_bytes(existing) != canonical_bytes(document):
            raise GovernedWorkError(
                f"immutable id is already bound to another envelope: {path.stem}"
            )
        return False
    _atomic_json(path, document)
    return True


def _normalised_spec_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def full_spec_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(
        _normalised_spec_text(text).encode("utf-8")
    ).hexdigest()


def _load_spec_inputs(
    target_dir: str,
    *,
    spec_id: str,
    requirement_ids: Optional[Sequence[str]],
    spec_path: Optional[str],
) -> tuple[str, List[Dict[str, str]]]:
    from apatch.spec import _load_spec

    parsed = _load_spec(target_dir, spec=spec_id, spec_path=spec_path)
    if not parsed.source_path:
        raise GovernedWorkError("SPEC source path is required")
    try:
        text = Path(parsed.source_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise GovernedWorkError("cannot read SPEC source") from exc
    selected = (
        list(requirement_ids)
        if requirement_ids is not None
        else [requirement.id for requirement in parsed.requirements]
    )
    if not selected:
        raise GovernedWorkError("at least one requirement is required")
    by_id = {requirement.id: requirement for requirement in parsed.requirements}
    unknown = sorted(set(selected) - set(by_id))
    if unknown:
        raise GovernedWorkError(f"unknown SPEC requirements: {unknown}")
    if len(selected) != len(set(selected)):
        raise GovernedWorkError("requirement_ids must be duplicate-free")
    refs = [
        {
            "requirement_id": requirement_id,
            "requirement_hash": by_id[requirement_id].content_hash,
        }
        for requirement_id in sorted(selected)
    ]
    return full_spec_hash(text), refs


def validate_change(
    document: Mapping[str, Any],
    *,
    trusted_actor_keys: Optional[Mapping[str, str | bytes]] = None,
) -> Dict[str, Any]:
    change = _expect_exact_keys(dict(document), _CHANGE_KEYS, "Change")
    if change["schema"] != CHANGE_SCHEMA or change["execution_system"] != "apatch":
        raise GovernedWorkError("invalid APatch Change schema or execution_system")
    _matches(change["change_id"], _CHANGE_ID_RE, "change_id")
    _required_text(change["tenant_id"], "tenant_id")
    _matches(change["project_group_id"], _GROUP_ID_RE, "project_group_id")
    if change["source_kind"] != "work_program":
        raise GovernedWorkError("Change source_kind must be work_program")
    _matches(change["work_program_id"], _PROGRAM_ID_RE, "work_program_id")
    _matches(change["work_program_hash"], _HASH64_RE, "work_program_hash")
    _nullable_pair(
        change["context_release_id"],
        change["context_release_manifest_hash"],
        "context_release",
    )
    _required_text(change["spec_id"], "spec_id")
    _matches(change["spec_hash"], _HASH64_RE, "spec_hash")
    _validate_requirement_refs(change["requirement_refs"])
    _matches(change["purpose_hash"], _HASH64_RE, "purpose_hash")
    actor_key_id = _required_text(change["actor_key_id"], "actor_key_id")
    _timestamp(change["issued_at"], "issued_at")
    _validate_signature_shape(change)
    if change["signature"]["key_id"] != actor_key_id:
        raise GovernedWorkError("Change actor_key_id must match signature.key_id")
    assert_privacy_safe(change)
    if trusted_actor_keys is not None:
        verify_document_signature(change, trusted_actor_keys)
    return change


def prepare_change(
    target_dir: str = ".",
    *,
    tenant_id: str,
    project_group_id: str,
    work_program_id: str,
    work_program_hash: str,
    spec_id: str,
    purpose: str,
    requirement_ids: Optional[Sequence[str]] = None,
    spec_path: Optional[str] = None,
    context_release_id: Optional[str] = None,
    context_release_manifest_hash: Optional[str] = None,
    issued_at: Optional[str] = None,
    key_provider: Any = None,
    store: bool = True,
) -> Dict[str, Any]:
    provider = _load_local_provider(target_dir, key_provider)
    actor_key_id = signer_key_id(provider)
    spec_hash, requirement_refs = _load_spec_inputs(
        target_dir,
        spec_id=spec_id,
        requirement_ids=requirement_ids,
        spec_path=spec_path,
    )
    normalized_purpose = " ".join(_required_text(purpose, "purpose").split())
    seed = {
        "tenant_id": tenant_id,
        "project_group_id": project_group_id,
        "source_kind": "work_program",
        "work_program_id": work_program_id,
        "work_program_hash": work_program_hash,
        "context_release_id": context_release_id,
        "context_release_manifest_hash": context_release_manifest_hash,
        "spec_id": spec_id,
        "spec_hash": spec_hash,
        "requirement_refs": requirement_refs,
        "purpose_hash": value_hash(normalized_purpose),
        "actor_key_id": actor_key_id,
    }
    change_id = "apchg_" + value_hash(seed).split(":", 1)[1][:32]
    path = governed_work_root(target_dir) / "changes" / f"{change_id}.json"
    if store and path.exists():
        existing = _read_document(path)
        validate_change(existing, trusted_actor_keys={actor_key_id: provider.get_public_key()})
        for key, value in seed.items():
            if existing.get(key) != value:
                raise GovernedWorkError("stored Change conflicts with deterministic identity")
        return {
            "ok": True,
            "stored": False,
            "change": existing,
            "change_hash": document_hash(existing),
            "envelope_hash": envelope_hash(existing),
        }
    body = {
        "schema": CHANGE_SCHEMA,
        "change_id": change_id,
        "execution_system": "apatch",
        **seed,
        "issued_at": issued_at or _utc_now(),
    }
    signed = sign_document(body, provider, key_id=actor_key_id)
    validate_change(signed, trusted_actor_keys={actor_key_id: provider.get_public_key()})
    stored = _immutable_write(path, signed) if store else False
    return {
        "ok": True,
        "stored": stored,
        "change": signed,
        "change_hash": document_hash(signed),
        "envelope_hash": envelope_hash(signed),
    }


def load_change(target_dir: str, change_id: str) -> Dict[str, Any]:
    _matches(change_id, _CHANGE_ID_RE, "change_id")
    change = _read_document(
        governed_work_root(target_dir) / "changes" / f"{change_id}.json"
    )
    return validate_change(change)


def validate_project_source_binding(
    document: Mapping[str, Any],
    *,
    change: Mapping[str, Any],
    trusted_authority_keys: Mapping[str, str | bytes],
    status: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    binding = _expect_exact_keys(
        dict(document), _SOURCE_BINDING_KEYS, "ProjectSourceBinding"
    )
    if binding["schema"] != SOURCE_BINDING_SCHEMA:
        raise GovernedWorkError("invalid ProjectSourceBinding schema")
    _matches(binding["binding_id"], _BINDING_ID_RE, "binding_id")
    _required_text(binding["tenant_id"], "tenant_id")
    _matches(binding["project_group_id"], _GROUP_ID_RE, "project_group_id")
    if binding["source_kind"] != "work_program":
        raise GovernedWorkError("ProjectSourceBinding source_kind must be work_program")
    _matches(binding["work_program_id"], _PROGRAM_ID_RE, "work_program_id")
    _matches(binding["work_program_hash"], _HASH64_RE, "work_program_hash")
    _nullable_pair(
        binding["context_release_id"],
        binding["context_release_manifest_hash"],
        "context_release",
    )
    if binding["execution_system"] != "apatch":
        raise GovernedWorkError("ProjectSourceBinding execution_system must be apatch")
    _matches(binding["change_id"], _CHANGE_ID_RE, "change_id")
    _matches(binding["change_hash"], _HASH64_RE, "change_hash")
    _required_text(binding["spec_id"], "spec_id")
    _matches(binding["spec_hash"], _HASH64_RE, "spec_hash")
    _validate_requirement_refs(binding["requirement_refs"])
    _required_text(binding["actor_ref"], "actor_ref")
    _integer(binding["authority_version"], "authority_version", minimum=1)
    _timestamp(binding["issued_at"], "issued_at")
    _validate_signature_shape(binding)
    assert_privacy_safe(binding)
    verify_platform_document_signature(
        binding,
        trusted_authority_keys,
        purpose=PLATFORM_SOURCE_BINDING_PURPOSE,
    )

    local = validate_change(change)
    exact_fields = (
        "tenant_id",
        "project_group_id",
        "source_kind",
        "work_program_id",
        "work_program_hash",
        "context_release_id",
        "context_release_manifest_hash",
        "execution_system",
        "change_id",
        "spec_id",
        "spec_hash",
        "requirement_refs",
    )
    for field in exact_fields:
        if binding[field] != local[field]:
            raise GovernedWorkError(f"ProjectSourceBinding mismatch: {field}")
    if binding["change_hash"] != document_hash(local):
        raise GovernedWorkError("ProjectSourceBinding mismatch: change_hash")

    if status is not None:
        source_state = str(status.get("source_verified") or "")
        if source_state in {"revoked", "invalid"}:
            raise GovernedWorkError(
                f"ProjectSourceBinding is not active: {source_state}"
            )
        status_binding_id = status.get("project_source_binding_id")
        if status_binding_id is not None and status_binding_id != binding["binding_id"]:
            raise GovernedWorkError("Platform status references another source binding")
    return binding


def source_binding_artifact(binding: Mapping[str, Any]) -> str:
    binding_id = _matches(binding.get("binding_id"), _BINDING_ID_RE, "binding_id")
    return f"project-source-binding:{binding_id}@{document_hash(binding)}"


def store_project_source_binding(
    target_dir: str,
    document: Mapping[str, Any],
    *,
    trusted_authority_keys: Mapping[str, str | bytes],
    change: Optional[Mapping[str, Any]] = None,
    status: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    change_id = _required_text(document.get("change_id"), "change_id")
    local_change = dict(change) if change is not None else load_change(target_dir, change_id)
    binding = validate_project_source_binding(
        document,
        change=local_change,
        trusted_authority_keys=trusted_authority_keys,
        status=status,
    )
    path = (
        governed_work_root(target_dir)
        / "bindings"
        / f"{binding['binding_id']}.json"
    )
    stored = _immutable_write(path, binding)
    return {
        "ok": True,
        "stored": stored,
        "binding_id": binding["binding_id"],
        "binding_hash": document_hash(binding),
        "session_artifact": source_binding_artifact(binding),
    }


def load_project_source_binding(target_dir: str, binding_id: str) -> Dict[str, Any]:
    _matches(binding_id, _BINDING_ID_RE, "binding_id")
    return _read_document(
        governed_work_root(target_dir) / "bindings" / f"{binding_id}.json"
    )


def _event_ref(event: Mapping[str, Any]) -> Dict[str, str]:
    event_id = _required_text(event.get("event_id"), "event_id")
    return {"event_id": event_id, "event_hash": document_hash(event)}


def validate_timesheet_draft(
    document: Mapping[str, Any],
    *,
    trusted_actor_keys: Optional[Mapping[str, str | bytes]] = None,
) -> Dict[str, Any]:
    draft = _expect_exact_keys(dict(document), _TIMESHEET_KEYS, "TimesheetDraft")
    if draft["schema"] != TIMESHEET_SCHEMA:
        raise GovernedWorkError("invalid TimesheetDraft schema")
    _matches(draft["timesheet_id"], _TIMESHEET_ID_RE, "timesheet_id")
    _matches(
        draft["project_source_binding_id"], _BINDING_ID_RE, "project_source_binding_id"
    )
    _matches(
        draft["project_source_binding_hash"], _HASH64_RE, "project_source_binding_hash"
    )
    subject_key_id = _required_text(draft["subject_key_id"], "subject_key_id")
    started = _timestamp(draft["period_started_at"], "period_started_at")
    ended = _timestamp(draft["period_ended_at"], "period_ended_at")
    if datetime.fromisoformat(ended.replace("Z", "+00:00")) < datetime.fromisoformat(
        started.replace("Z", "+00:00")
    ):
        raise GovernedWorkError("timesheet period is inverted")
    _integer(draft["claimed_active_seconds"], "claimed_active_seconds")
    if not isinstance(draft["session_refs"], list) or not draft["session_refs"]:
        raise GovernedWorkError("session_refs must be a nonempty array")
    session_refs = []
    for index, raw in enumerate(draft["session_refs"]):
        ref = _expect_exact_keys(raw, _SESSION_REF_KEYS, f"session_refs[{index}]")
        session_refs.append(
            {
                "governed_session_id": _required_text(
                    ref["governed_session_id"], "governed_session_id"
                ),
                "started_at": _timestamp(ref["started_at"], "started_at"),
                "ended_at": _timestamp(ref["ended_at"], "ended_at"),
            }
        )
    if session_refs != sorted(
        session_refs, key=lambda item: item["governed_session_id"]
    ):
        raise GovernedWorkError("session_refs must be sorted")
    if len({item["governed_session_id"] for item in session_refs}) != len(session_refs):
        raise GovernedWorkError("session_refs must be duplicate-free")
    _validate_contribution_refs(draft["contribution_event_refs"])
    _timestamp(draft["issued_at"], "issued_at")
    _validate_signature_shape(draft)
    if draft["signature"]["key_id"] != subject_key_id:
        raise GovernedWorkError("Timesheet subject_key_id must match signer")
    assert_privacy_safe(draft)
    if trusted_actor_keys is not None:
        verify_document_signature(draft, trusted_actor_keys)
    return draft


def build_timesheet_draft(
    target_dir: str,
    *,
    binding: Mapping[str, Any],
    sessions: Sequence[Mapping[str, Any]],
    contribution_events: Sequence[Mapping[str, Any]],
    claimed_active_seconds: Optional[int] = None,
    issued_at: Optional[str] = None,
    key_provider: Any = None,
    store: bool = True,
) -> Dict[str, Any]:
    provider = _load_local_provider(target_dir, key_provider)
    subject_key_id = signer_key_id(provider)
    if not sessions:
        raise GovernedWorkError("at least one source-bound session is required")
    session_refs = sorted(
        [
            {
                "governed_session_id": _required_text(
                    session.get("governed_session_id"), "governed_session_id"
                ),
                "started_at": _timestamp(session.get("started_at"), "started_at"),
                "ended_at": _timestamp(session.get("ended_at"), "ended_at"),
            }
            for session in sessions
        ],
        key=lambda item: item["governed_session_id"],
    )
    if len({item["governed_session_id"] for item in session_refs}) != len(session_refs):
        raise GovernedWorkError("source-bound sessions must be duplicate-free")
    contribution_refs = sorted(
        [_event_ref(event) for event in contribution_events],
        key=lambda item: item["event_id"],
    )
    _validate_contribution_refs(contribution_refs)
    if claimed_active_seconds is None:
        claimed = 0
        for event in contribution_events:
            session = event.get("session") if isinstance(event.get("session"), dict) else {}
            value = session.get("active_sec")
            if value is None:
                value = session.get("duration_sec")
            try:
                claimed += max(0, int(round(float(value or 0))))
            except (TypeError, ValueError):
                raise GovernedWorkError("ContributionEvent duration is invalid") from None
    else:
        claimed = _integer(claimed_active_seconds, "claimed_active_seconds")
    period_started_at = min(item["started_at"] for item in session_refs)
    period_ended_at = max(item["ended_at"] for item in session_refs)
    binding_id = _matches(binding.get("binding_id"), _BINDING_ID_RE, "binding_id")
    binding_hash = document_hash(binding)
    seed = {
        "project_source_binding_id": binding_id,
        "project_source_binding_hash": binding_hash,
        "subject_key_id": subject_key_id,
        "period_started_at": period_started_at,
        "period_ended_at": period_ended_at,
        "claimed_active_seconds": claimed,
        "session_refs": session_refs,
        "contribution_event_refs": contribution_refs,
    }
    timesheet_id = "apts_" + value_hash(seed).split(":", 1)[1][:32]
    body = {
        "schema": TIMESHEET_SCHEMA,
        "timesheet_id": timesheet_id,
        **seed,
        "issued_at": issued_at or _utc_now(),
    }
    signed = sign_document(body, provider, key_id=subject_key_id)
    validate_timesheet_draft(
        signed, trusted_actor_keys={subject_key_id: provider.get_public_key()}
    )
    stored = False
    if store:
        stored = _immutable_write(
            governed_work_root(target_dir)
            / "timesheets"
            / f"{timesheet_id}.json",
            signed,
        )
    return {"document": signed, "stored": stored}


def validate_work_evidence_bundle(
    document: Mapping[str, Any],
    *,
    trusted_actor_keys: Optional[Mapping[str, str | bytes]] = None,
) -> Dict[str, Any]:
    bundle = _expect_exact_keys(dict(document), _EVIDENCE_KEYS, "WorkEvidenceBundle")
    if bundle["schema"] != EVIDENCE_SCHEMA:
        raise GovernedWorkError("invalid WorkEvidenceBundle schema")
    _matches(bundle["bundle_id"], _BUNDLE_ID_RE, "bundle_id")
    _matches(bundle["change_id"], _CHANGE_ID_RE, "change_id")
    _matches(bundle["change_hash"], _HASH64_RE, "change_hash")
    _matches(
        bundle["project_source_binding_id"], _BINDING_ID_RE, "project_source_binding_id"
    )
    _matches(
        bundle["project_source_binding_hash"], _HASH64_RE, "project_source_binding_hash"
    )
    _required_text(bundle["spec_id"], "spec_id")
    _matches(bundle["spec_hash"], _HASH64_RE, "spec_hash")
    _validate_requirement_refs(bundle["requirement_refs"])
    if not isinstance(bundle["attestation_refs"], list) or not bundle["attestation_refs"]:
        raise GovernedWorkError("attestation_refs must be a nonempty array")
    attestation_refs = []
    for index, raw in enumerate(bundle["attestation_refs"]):
        ref = _expect_exact_keys(
            raw, _ATTESTATION_REF_KEYS, f"attestation_refs[{index}]"
        )
        outcome = str(ref["outcome"])
        if outcome not in {"passed", "rolled_back"}:
            raise GovernedWorkError("attestation outcome must be passed or rolled_back")
        attestation_refs.append(
            {
                "spec_id": _required_text(ref["spec_id"], "spec_id"),
                "requirement_id": _matches(
                    ref["requirement_id"], _REQUIREMENT_ID_RE, "requirement_id"
                ),
                "attestation_id": _required_text(
                    ref["attestation_id"], "attestation_id"
                ),
                "attestation_hash": _matches(
                    ref["attestation_hash"], _HASH64_RE, "attestation_hash"
                ),
                "outcome": outcome,
            }
        )
    if attestation_refs != sorted(
        attestation_refs,
        key=lambda item: (item["spec_id"], item["requirement_id"], item["attestation_id"]),
    ):
        raise GovernedWorkError("attestation_refs must be sorted")
    _validate_contribution_refs(bundle["contribution_event_refs"])
    timesheet_ref = _expect_exact_keys(
        bundle["timesheet_ref"], _TIMESHEET_REF_KEYS, "timesheet_ref"
    )
    _matches(timesheet_ref["timesheet_id"], _TIMESHEET_ID_RE, "timesheet_id")
    _matches(timesheet_ref["timesheet_hash"], _HASH64_RE, "timesheet_hash")
    _integer(timesheet_ref["claimed_active_seconds"], "claimed_active_seconds")
    _timestamp(bundle["issued_at"], "issued_at")
    _validate_signature_shape(bundle)
    assert_privacy_safe(bundle)
    if trusted_actor_keys is not None:
        verify_document_signature(bundle, trusted_actor_keys)
    return bundle


_INTERNAL_ATTESTATION_FACT_KEYS = frozenset(
    set(_ATTESTATION_REF_KEYS)
    | {
        "requirement_hash",
        "project_source_binding_id",
        "project_source_binding_hash",
        "governed_session_id",
    }
)


def build_work_evidence_bundle(
    target_dir: str,
    *,
    change: Mapping[str, Any],
    binding: Mapping[str, Any],
    attestation_facts: Sequence[Mapping[str, Any]],
    contribution_events: Sequence[Mapping[str, Any]],
    timesheet: Mapping[str, Any],
    issued_at: Optional[str] = None,
    key_provider: Any = None,
    store: bool = True,
) -> Dict[str, Any]:
    provider = _load_local_provider(target_dir, key_provider)
    actor_key_id = signer_key_id(provider)
    local_change = validate_change(change)
    binding_id = _matches(binding.get("binding_id"), _BINDING_ID_RE, "binding_id")
    binding_hash = document_hash(binding)
    if binding.get("change_id") != local_change["change_id"]:
        raise GovernedWorkError("binding references another Change")
    if binding.get("change_hash") != document_hash(local_change):
        raise GovernedWorkError("binding change_hash does not match local Change")
    if binding.get("spec_id") != local_change["spec_id"]:
        raise GovernedWorkError("binding spec_id does not match local Change")
    if binding.get("spec_hash") != local_change["spec_hash"]:
        raise GovernedWorkError("binding spec_hash does not match local Change")

    expected_requirements = {
        item["requirement_id"]: item["requirement_hash"]
        for item in _validate_requirement_refs(binding.get("requirement_refs"))
    }
    refs = []
    covered = set()
    for index, raw in enumerate(attestation_facts):
        fact = _expect_exact_keys(
            dict(raw), _INTERNAL_ATTESTATION_FACT_KEYS, f"attestation_facts[{index}]"
        )
        requirement_id = _matches(
            fact["requirement_id"], _REQUIREMENT_ID_RE, "requirement_id"
        )
        if fact["spec_id"] != binding["spec_id"]:
            raise GovernedWorkError("attestation belongs to another SPEC")
        if requirement_id not in expected_requirements:
            raise GovernedWorkError("attestation requirement is not source-bound")
        if fact["requirement_hash"] != expected_requirements[requirement_id]:
            raise GovernedWorkError(f"stale attestation for {requirement_id}")
        if (
            fact["project_source_binding_id"] != binding_id
            or fact["project_source_binding_hash"] != binding_hash
        ):
            raise GovernedWorkError("attestation is not bound to the exact source binding")
        if fact["outcome"] != "passed":
            raise GovernedWorkError("rolled-back evidence cannot qualify contribution")
        _required_text(fact["governed_session_id"], "governed_session_id")
        refs.append(
            {
                "spec_id": fact["spec_id"],
                "requirement_id": requirement_id,
                "attestation_id": _required_text(
                    fact["attestation_id"], "attestation_id"
                ),
                "attestation_hash": _matches(
                    fact["attestation_hash"], _HASH64_RE, "attestation_hash"
                ),
                "outcome": "passed",
            }
        )
        covered.add(requirement_id)
    if covered != set(expected_requirements):
        missing = sorted(set(expected_requirements) - covered)
        raise GovernedWorkError(f"missing current attestations: {missing}")
    refs.sort(
        key=lambda item: (item["spec_id"], item["requirement_id"], item["attestation_id"])
    )
    contribution_refs = sorted(
        [_event_ref(event) for event in contribution_events],
        key=lambda item: item["event_id"],
    )
    _validate_contribution_refs(contribution_refs)
    validated_timesheet = validate_timesheet_draft(timesheet)
    if (
        validated_timesheet["project_source_binding_id"] != binding_id
        or validated_timesheet["project_source_binding_hash"] != binding_hash
    ):
        raise GovernedWorkError("timesheet is bound to another source")
    if validated_timesheet["contribution_event_refs"] != contribution_refs:
        raise GovernedWorkError("timesheet contribution refs do not match evidence")
    body_seed = {
        "change_id": local_change["change_id"],
        "change_hash": document_hash(local_change),
        "project_source_binding_id": binding_id,
        "project_source_binding_hash": binding_hash,
        "spec_id": binding["spec_id"],
        "spec_hash": binding["spec_hash"],
        "requirement_refs": binding["requirement_refs"],
        "attestation_refs": refs,
        "contribution_event_refs": contribution_refs,
        "timesheet_ref": {
            "timesheet_id": validated_timesheet["timesheet_id"],
            "timesheet_hash": document_hash(validated_timesheet),
            "claimed_active_seconds": validated_timesheet["claimed_active_seconds"],
        },
    }
    bundle_id = "apweb_" + value_hash(body_seed).split(":", 1)[1][:32]
    body = {
        "schema": EVIDENCE_SCHEMA,
        "bundle_id": bundle_id,
        **body_seed,
        "issued_at": issued_at or _utc_now(),
    }
    signed = sign_document(body, provider, key_id=actor_key_id)
    validate_work_evidence_bundle(
        signed, trusted_actor_keys={actor_key_id: provider.get_public_key()}
    )
    stored = False
    if store:
        stored = _immutable_write(
            governed_work_root(target_dir) / "evidence" / f"{bundle_id}.json",
            signed,
        )
    return {"document": signed, "stored": stored}


def _artifact_matches(
    artifacts: Any,
    *,
    kind: str,
    identifier: str,
    content_hash: str,
) -> bool:
    if not isinstance(artifacts, list):
        return False
    for raw in artifacts:
        if isinstance(raw, str):
            expected = f"{kind}:{identifier}@{content_hash}"
            if raw == expected:
                return True
        elif isinstance(raw, dict):
            if (
                raw.get("kind") == kind
                and raw.get("id") == identifier
                and raw.get("content_hash") == content_hash
            ):
                return True
    return False


def derive_source_bound_attestation_facts(
    rows: Sequence[Mapping[str, Any]],
    *,
    binding: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    binding_id = _matches(binding.get("binding_id"), _BINDING_ID_RE, "binding_id")
    binding_hash = document_hash(binding)
    spec_id = _required_text(binding.get("spec_id"), "spec_id")
    expected = {
        item["requirement_id"]: item["requirement_hash"]
        for item in _validate_requirement_refs(binding.get("requirement_refs"))
    }
    rolled_back_sessions = {
        str((row.get("payload") or {}).get("governed_session_id") or "")
        for row in rows
        if isinstance(row.get("payload"), dict)
        and str((row.get("payload") or {}).get("action") or "")
        in {"rollback", "rollback_compensation"}
    }
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("tool_id") != "apatch_attest":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        artifacts = payload.get("artifacts")
        if not _artifact_matches(
            artifacts,
            kind="project-source-binding",
            identifier=binding_id,
            content_hash=binding_hash,
        ):
            continue
        session_id = str(
            payload.get("governed_session_id") or payload.get("session_id") or ""
        )
        for requirement_id, requirement_hash in expected.items():
            if not _artifact_matches(
                artifacts,
                kind="spec",
                identifier=f"{spec_id}#{requirement_id}",
                content_hash=requirement_hash,
            ):
                continue
            attestation_id = str(row.get("id") or "")
            if not attestation_id:
                attestation_id = hashlib.sha256(
                    str(row.get("signature") or "").encode("utf-8")
                ).hexdigest()
            projection = {
                "spec_id": spec_id,
                "requirement_id": requirement_id,
                "requirement_hash": requirement_hash,
                "attestation_id": attestation_id,
                "governed_session_id": session_id,
                "ledger_signature_hash": value_hash(str(row.get("signature") or "")),
                "timestamp": str(row.get("timestamp") or ""),
                "outcome": (
                    "rolled_back" if session_id in rolled_back_sessions else "passed"
                ),
            }
            latest[requirement_id] = {
                "spec_id": spec_id,
                "requirement_id": requirement_id,
                "requirement_hash": requirement_hash,
                "attestation_id": attestation_id,
                "attestation_hash": value_hash(projection),
                "outcome": projection["outcome"],
                "project_source_binding_id": binding_id,
                "project_source_binding_hash": binding_hash,
                "governed_session_id": session_id,
            }
    missing = sorted(set(expected) - set(latest))
    if missing:
        raise GovernedWorkError(f"missing source-bound attestations: {missing}")
    return [latest[requirement_id] for requirement_id in sorted(latest)]


def _contribution_public_key(
    target_dir: str,
    event: Mapping[str, Any],
    *,
    key_provider: Any = None,
) -> bytes:
    identity = (
        event.get("identity")
        if isinstance(event.get("identity"), dict)
        else {}
    )
    key_id = _required_text(
        identity.get("key_id"),
        "ContributionEvent identity.key_id",
    )
    encoded = identity.get("public_key")
    if encoded is not None:
        if not isinstance(encoded, str) or not encoded:
            raise GovernedWorkError("ContributionEvent public key is invalid")
        try:
            public_raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise GovernedWorkError("ContributionEvent public key is invalid") from exc
    else:
        provider = _load_local_provider(target_dir, key_provider)
        try:
            public_raw = provider.get_public_key()
        except Exception as exc:
            raise GovernedWorkError(
                "trusted ContributionEvent signer key is unavailable"
            ) from exc

    if not isinstance(public_raw, bytes) or len(public_raw) != 32:
        raise GovernedWorkError("ContributionEvent public key is invalid")
    if hashlib.sha256(public_raw).hexdigest()[:32] != key_id:
        raise GovernedWorkError("ContributionEvent signer key_id mismatch")
    return public_raw


def _load_source_bound_contributions(
    target_dir: str,
    *,
    session_ids: set[str],
    binding: Mapping[str, Any],
    store_dir: Optional[str] = None,
    key_provider: Any = None,
) -> List[Dict[str, Any]]:
    from apatch.contribution import verify_event
    from apatch.timesheet import load_events

    binding_id = str(binding["binding_id"])
    binding_hash = document_hash(binding)
    selected: Dict[str, Dict[str, Any]] = {}
    for event in load_events(store_dir):
        session = event.get("session") if isinstance(event.get("session"), dict) else {}
        session_id = str(session.get("session_id") or "")
        if session_id not in session_ids:
            continue
        if not _artifact_matches(
            session.get("artifacts"),
            kind="project-source-binding",
            identifier=binding_id,
            content_hash=binding_hash,
        ):
            raise GovernedWorkError(
                f"ContributionEvent {event.get('event_id')} is not source-bound"
            )
        signature = event.get("signature")
        if not isinstance(signature, str) or not signature:
            raise GovernedWorkError("source-bound ContributionEvent must be signed")
        public_raw = _contribution_public_key(
            target_dir,
            event,
            key_provider=key_provider,
        )
        if not verify_event(event, public_raw):
            raise GovernedWorkError("ContributionEvent signature verification failed")
        selected[session_id] = event
    missing = sorted(session_ids - set(selected))
    if missing:
        raise GovernedWorkError(f"missing ContributionEvents for sessions: {missing}")
    return [selected[session_id] for session_id in sorted(selected)]


def _source_bound_session_refs(
    contribution_events: Sequence[Mapping[str, Any]],
) -> List[Dict[str, str]]:
    refs = []
    for event in contribution_events:
        session = event.get("session") if isinstance(event.get("session"), dict) else {}
        refs.append(
            {
                "governed_session_id": _required_text(
                    session.get("session_id"),
                    "governed_session_id",
                ),
                "started_at": _canonical_timestamp(
                    session.get("started_at"),
                    "started_at",
                ),
                "ended_at": _canonical_timestamp(
                    session.get("ended_at") or event.get("created_at"),
                    "ended_at",
                ),
            }
        )
    return refs


def build_workspace_evidence(
    target_dir: str,
    *,
    binding_id: str,
    contribution_store_dir: Optional[str] = None,
    key_provider: Any = None,
    issued_at: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.trustchain_helper import TrustChainHelper

    provider = _load_local_provider(target_dir, key_provider)
    binding = load_project_source_binding(target_dir, binding_id)
    change = load_change(target_dir, str(binding["change_id"]))
    rows = TrustChainHelper(target_dir).iter_ledger_entries()
    facts = derive_source_bound_attestation_facts(rows, binding=binding)
    if any(fact["outcome"] != "passed" for fact in facts):
        raise GovernedWorkError("rolled-back evidence cannot be admitted")
    session_ids = {str(fact["governed_session_id"]) for fact in facts}
    events = _load_source_bound_contributions(
        target_dir,
        session_ids=session_ids,
        binding=binding,
        store_dir=contribution_store_dir,
        key_provider=provider,
    )
    sessions = _source_bound_session_refs(events)
    timesheet_result = build_timesheet_draft(
        target_dir,
        binding=binding,
        sessions=sessions,
        contribution_events=events,
        key_provider=provider,
        issued_at=issued_at,
    )
    evidence_result = build_work_evidence_bundle(
        target_dir,
        change=change,
        binding=binding,
        attestation_facts=facts,
        contribution_events=events,
        timesheet=timesheet_result["document"],
        key_provider=provider,
        issued_at=issued_at,
    )
    return {
        "ok": True,
        "binding_id": binding_id,
        "binding_hash": document_hash(binding),
        "change_id": change["change_id"],
        "change_hash": document_hash(change),
        "timesheet": timesheet_result["document"],
        "timesheet_hash": document_hash(timesheet_result["document"]),
        "evidence_bundle": evidence_result["document"],
        "evidence_bundle_hash": document_hash(evidence_result["document"]),
        "attestation_count": len(facts),
        "contribution_event_count": len(events),
    }


def local_governed_work_status(target_dir: str = ".") -> Dict[str, Any]:
    root = governed_work_root(target_dir)

    def count(name: str) -> int:
        directory = root / name
        return len(list(directory.glob("*.json"))) if directory.is_dir() else 0

    return {
        "ok": True,
        "schema": "apatch.governed-work-local-status.v1",
        "changes": count("changes"),
        "source_bindings": count("bindings"),
        "timesheet_drafts": count("timesheets"),
        "evidence_bundles": count("evidence"),
        "collective_acceptance": "unknown",
        "source_verified": "local_only" if count("bindings") else "unbound",
        "contribution_bound": "unknown",
        "timesheet_accepted": "unknown",
    }
