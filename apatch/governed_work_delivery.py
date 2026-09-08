"""Durable offline-first delivery for APatch governed-work documents."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import urlsplit

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

from apatch import governed_work_transport as T
from apatch.governed_work import (
    GovernedWorkError,
    _BUNDLE_ID_RE,
    _GROUP_ID_RE,
    _HASH64_RE,
    _SIGNATURE_KEYS,
    _atomic_json,
    _expect_exact_keys,
    _immutable_write,
    _integer,
    _matches,
    _read_document,
    _required_text,
    _timestamp,
    assert_privacy_safe,
    canonical_bytes,
    document_hash,
    governed_work_root,
    sign_document,
    signer_key_id,
    store_project_source_binding,
    validate_timesheet_draft,
    validate_work_evidence_bundle,
    value_hash,
    verify_document_signature,
)
from apatch.runtime.atomic_io import exclusive_file_lock


OUTBOX_SCHEMA = "apatch.governed-work-outbox-entry.v1"
RECEIPT_SCHEMA = "trustchain.governed-work-admission-receipt.v1"
CONFIG_SCHEMA = "apatch.governed-work-config.v1"
CONFIG_TRANSITION_SCHEMA = "apatch.governed-work-endpoint-transition.v1"
OUTBOX_RETIREMENT_SCHEMA = "apatch.governed-work-outbox-retirement.v1"

_ENTRY_ID_RE = re.compile(r"^apgwo_[0-9a-f]{32}$")
_RECEIPT_ID_RE = re.compile(r"^tcgwar_[0-9a-f]{32}$")
_TRANSITION_ID_RE = re.compile(r"^apgwct_[0-9a-f]{32}$")
_RETIREMENT_ID_RE = re.compile(r"^apgwor_[0-9a-f]{32}$")

_OUTBOX_KEYS = frozenset(
    {
        "schema",
        "entry_id",
        "command",
        "tenant_id",
        "project_group_id",
        "client_id",
        "idempotency_key",
        "request_hash",
        "endpoint",
        "payload",
        "created_at",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "receipt_id",
        "tenant_id",
        "project_group_id",
        "request_hash",
        "command",
        "resource_id",
        "resource_hash",
        "projection_cursor",
        "accepted_at",
        "signature",
    }
)
_CONFIG_KEYS = frozenset(
    {
        "schema",
        "platform_url",
        "client_id",
        "service_request_key_id",
        "binding_authority_keys",
    }
)
_TRANSITION_KEYS = frozenset(
    {
        "schema",
        "transition_id",
        "idempotency_key_hash",
        "client_id",
        "service_request_key_id",
        "previous_platform_url",
        "platform_url",
        "previous_config_hash",
        "config_hash",
        "changed_at",
        "signature",
    }
)
_RETIREMENT_KEYS = frozenset(
    {
        "schema",
        "retirement_id",
        "entry_id",
        "entry_hash",
        "reason_code",
        "superseded_by_entry_id",
        "superseded_by_entry_hash",
        "retired_at",
        "signature",
    }
)

_STATUS_KEYS = frozenset(
    {
        "schema",
        "tenant_id",
        "project_group_id",
        "work_program_id",
        "work_release_id",
        "collective_acceptance",
        "source_verified",
        "contribution_bound",
        "timesheet_accepted",
        "authority_version",
        "revocation_version",
        "projection_cursor",
    }
)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def config_path(target_dir: str = ".") -> Path:
    return Path(os.path.abspath(target_dir)) / ".apatch" / "governed_work.json"


def _normalize_platform_url(value: Any) -> str:
    platform_url = _required_text(value, "platform_url").rstrip("/")
    parsed = urlsplit(platform_url)
    localhost_http = (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
    )
    if parsed.scheme != "https" and not localhost_http:
        raise GovernedWorkError(
            "platform_url must use HTTPS (localhost HTTP is allowed for development)"
        )
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GovernedWorkError(
            "platform_url must be a credential-free base URL without query or fragment"
        )
    return platform_url


def validate_config(raw: Mapping[str, Any]) -> Dict[str, Any]:
    config = _expect_exact_keys(dict(raw), _CONFIG_KEYS, "governed-work config")
    if config["schema"] != CONFIG_SCHEMA:
        raise GovernedWorkError("invalid governed-work config schema")
    config["platform_url"] = _normalize_platform_url(config["platform_url"])
    _required_text(config["client_id"], "client_id")
    request_key_id = _required_text(
        config["service_request_key_id"], "service_request_key_id"
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", request_key_id):
        raise GovernedWorkError(
            "service_request_key_id must be a full Ed25519 public-key fingerprint"
        )
    keys = config["binding_authority_keys"]
    if not isinstance(keys, dict) or not keys:
        raise GovernedWorkError("binding_authority_keys must be a nonempty object")
    for key_id, public_key in keys.items():
        _required_text(key_id, "binding authority key id")
        _required_text(public_key, "binding authority public key")
    return config


def load_config(
    target_dir: str = ".",
    *,
    override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if override is not None:
        return validate_config(override)
    path = config_path(target_dir)
    if not path.is_file():
        raise GovernedWorkError(
            "governed-work config is missing; initialize .apatch/governed_work.json"
        )
    return validate_config(_read_document(path))


def write_config(target_dir: str, config: Mapping[str, Any]) -> Dict[str, Any]:
    validated = validate_config(config)
    path = config_path(target_dir)
    stored = _immutable_write(path, validated)
    return {
        "ok": True,
        "stored": stored,
        "schema": CONFIG_SCHEMA,
        "client_id": validated["client_id"],
        "service_request_key_id": validated["service_request_key_id"],
        "authority_key_count": len(validated["binding_authority_keys"]),
    }


def _transition_path(target_dir: str, idempotency_key_hash: str) -> Path:
    slug = idempotency_key_hash.split(":", 1)[1]
    return (
        governed_work_root(target_dir)
        / "config_transitions"
        / f"{slug}.json"
    )


def _local_signing_provider(target_dir: str, key_provider: Any = None) -> Any:
    if key_provider is not None:
        provider = key_provider
    else:
        from apatch.trust_identity import load_local_identity

        identity = load_local_identity(target_dir)
        provider = getattr(identity, "key_provider", None) if identity else None
    if provider is None:
        raise GovernedWorkError(
            "an enrolled APatch Ed25519 identity is required for local governance"
        )
    signer_key_id(provider)
    return provider


def _validate_transition(
    raw: Mapping[str, Any],
    *,
    key_provider: Any,
) -> Dict[str, Any]:
    transition = _expect_exact_keys(
        dict(raw),
        _TRANSITION_KEYS,
        "governed-work endpoint transition",
    )
    if transition["schema"] != CONFIG_TRANSITION_SCHEMA:
        raise GovernedWorkError("invalid endpoint transition schema")
    _matches(transition["transition_id"], _TRANSITION_ID_RE, "transition_id")
    _matches(
        transition["idempotency_key_hash"],
        _HASH64_RE,
        "idempotency_key_hash",
    )
    _required_text(transition["client_id"], "client_id")
    _matches(
        transition["service_request_key_id"],
        _HASH64_RE,
        "service_request_key_id",
    )
    transition["previous_platform_url"] = _normalize_platform_url(
        transition["previous_platform_url"]
    )
    transition["platform_url"] = _normalize_platform_url(
        transition["platform_url"]
    )
    _matches(
        transition["previous_config_hash"],
        _HASH64_RE,
        "previous_config_hash",
    )
    _matches(transition["config_hash"], _HASH64_RE, "config_hash")
    _timestamp(transition["changed_at"], "changed_at")
    local_key_id = signer_key_id(key_provider)
    verify_document_signature(
        transition,
        {local_key_id: key_provider.get_public_key()},
    )
    return transition


def transition_platform_url(
    target_dir: str,
    *,
    expected_platform_url: str,
    platform_url: str,
    idempotency_key: str,
    changed_at: Optional[str] = None,
    key_provider: Any = None,
) -> Dict[str, Any]:
    """CAS-update the signed-service endpoint with crash-safe replay."""
    expected_url = _normalize_platform_url(expected_platform_url)
    next_url = _normalize_platform_url(platform_url)
    if expected_url == next_url:
        raise GovernedWorkError("endpoint transition must change platform_url")
    idempotency = _required_text(idempotency_key, "idempotency_key")
    if not 16 <= len(idempotency) <= 200:
        raise GovernedWorkError(
            "idempotency_key must contain between 16 and 200 characters"
        )

    provider = _local_signing_provider(target_dir, key_provider)
    local_key_id = signer_key_id(provider)
    idempotency_hash = value_hash(idempotency)
    transition_id = "apgwct_" + idempotency_hash.split(":", 1)[1][:32]
    transition_path = _transition_path(target_dir, idempotency_hash)
    path = config_path(target_dir)

    with exclusive_file_lock(str(path)):
        current = load_config(target_dir)
        current_url = current["platform_url"]

        if transition_path.is_file():
            transition = _validate_transition(
                _read_document(transition_path),
                key_provider=provider,
            )
            expected_fields = {
                "transition_id": transition_id,
                "idempotency_key_hash": idempotency_hash,
                "client_id": current["client_id"],
                "service_request_key_id": current["service_request_key_id"],
                "previous_platform_url": expected_url,
                "platform_url": next_url,
            }
            for field, expected_value in expected_fields.items():
                if transition[field] != expected_value:
                    raise GovernedWorkError(
                        f"endpoint transition idempotency conflict: {field}"
                    )

            next_config = validate_config(
                {**current, "platform_url": next_url}
            )
            current_hash = value_hash(current)
            recovered = False
            if current_url == expected_url:
                if current_hash != transition["previous_config_hash"]:
                    raise GovernedWorkError(
                        "endpoint transition previous config hash mismatch"
                    )
                if value_hash(next_config) != transition["config_hash"]:
                    raise GovernedWorkError(
                        "endpoint transition next config hash mismatch"
                    )
                _atomic_json(path, next_config)
                recovered = True
            elif current_url == next_url:
                if current_hash != transition["config_hash"]:
                    raise GovernedWorkError(
                        "endpoint transition applied config hash mismatch"
                    )
            else:
                raise GovernedWorkError(
                    "platform_url changed outside the requested transition"
                )
            return {
                "ok": True,
                "stored": False,
                "recovered": recovered,
                "transition_id": transition["transition_id"],
                "transition_hash": document_hash(transition),
                "previous_platform_url": expected_url,
                "platform_url": next_url,
                "config_hash": transition["config_hash"],
                "signer_key_id": local_key_id,
            }

        if current_url != expected_url:
            raise GovernedWorkError(
                "platform_url does not match expected_platform_url"
            )
        next_config = validate_config({**current, "platform_url": next_url})
        unsigned_transition = {
            "schema": CONFIG_TRANSITION_SCHEMA,
            "transition_id": transition_id,
            "idempotency_key_hash": idempotency_hash,
            "client_id": current["client_id"],
            "service_request_key_id": current["service_request_key_id"],
            "previous_platform_url": expected_url,
            "platform_url": next_url,
            "previous_config_hash": value_hash(current),
            "config_hash": value_hash(next_config),
            "changed_at": _timestamp(
                changed_at or _utc_now(),
                "changed_at",
            ),
        }
        transition = sign_document(
            unsigned_transition,
            provider,
            key_id=local_key_id,
        )
        _immutable_write(transition_path, transition)
        _atomic_json(path, next_config)
        return {
            "ok": True,
            "stored": True,
            "recovered": False,
            "transition_id": transition_id,
            "transition_hash": document_hash(transition),
            "previous_platform_url": expected_url,
            "platform_url": next_url,
            "config_hash": value_hash(next_config),
            "signer_key_id": local_key_id,
        }


def _entry_path(target_dir: str, idempotency_key: str) -> Path:
    slug = value_hash(idempotency_key).split(":", 1)[1]
    return governed_work_root(target_dir) / "outbox" / f"{slug}.json"


def _ack_path(entry_path: Path) -> Path:
    return entry_path.with_name(entry_path.stem + ".ack.json")


def _retirement_path(entry_path: Path) -> Path:
    return entry_path.with_name(entry_path.stem + ".retired.json")


def _queue(
    target_dir: str,
    *,
    command: str,
    tenant_id: str,
    project_group_id: str,
    client_id: str,
    idempotency_key: str,
    request_hash: str,
    endpoint: str,
    payload: Mapping[str, Any],
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    request_hash = _matches(request_hash, _HASH64_RE, "request_hash")
    entry_id = "apgwo_" + request_hash.split(":", 1)[1][:32]
    entry = {
        "schema": OUTBOX_SCHEMA,
        "entry_id": entry_id,
        "command": command,
        "tenant_id": tenant_id,
        "project_group_id": project_group_id,
        "client_id": client_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "endpoint": endpoint,
        "payload": dict(payload),
        "created_at": created_at or _utc_now(),
    }
    path = _entry_path(target_dir, idempotency_key)
    if path.exists():
        existing = _read_document(path)
        if (
            existing.get("idempotency_key") != idempotency_key
            or existing.get("request_hash") != request_hash
            or canonical_bytes(existing.get("payload"))
            != canonical_bytes(entry["payload"])
        ):
            raise GovernedWorkError(
                "idempotency key is already bound to another request hash"
            )
        retired = (
            _retirement_is_active(target_dir, path, existing)
            if _retirement_path(path).is_file()
            else False
        )
        return {
            "ok": True,
            "stored": False,
            "entry_id": existing["entry_id"],
            "request_hash": request_hash,
            "pending": not _ack_path(path).exists() and not retired,
            "retired": retired,
        }
    _atomic_json(path, entry)
    return {
        "ok": True,
        "stored": True,
        "entry_id": entry_id,
        "request_hash": request_hash,
        "pending": True,
    }


def queue_source_binding_request(
    target_dir: str,
    *,
    change: Mapping[str, Any],
    client_id: str,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.governed_work import validate_change

    validated = validate_change(change)
    assert_privacy_safe(validated)
    group_id = validated["project_group_id"]
    return _queue(
        target_dir,
        command="source_binding",
        tenant_id=validated["tenant_id"],
        project_group_id=group_id,
        client_id=client_id,
        idempotency_key=f"source-binding:{validated['change_id']}",
        request_hash=value_hash(
            {"command": "create_source_binding", "payload": validated}
        ),
        endpoint=(
            f"/api/internal/project-groups/{group_id}/"
            "governed-work/source-bindings"
        ),
        payload={"change": validated},
        created_at=created_at,
    )


def queue_evidence_admission(
    target_dir: str,
    *,
    evidence_bundle: Mapping[str, Any],
    timesheet_draft: Mapping[str, Any],
    tenant_id: str,
    project_group_id: str,
    client_id: str,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    bundle = validate_work_evidence_bundle(evidence_bundle)
    timesheet = validate_timesheet_draft(timesheet_draft)
    assert_privacy_safe(bundle)
    assert_privacy_safe(timesheet)
    if bundle["timesheet_ref"]["timesheet_id"] != timesheet["timesheet_id"]:
        raise GovernedWorkError("evidence and timesheet ids differ")
    if bundle["timesheet_ref"]["timesheet_hash"] != document_hash(timesheet):
        raise GovernedWorkError("evidence and timesheet hashes differ")
    group_id = _matches(project_group_id, _GROUP_ID_RE, "project_group_id")
    return _queue(
        target_dir,
        command="evidence_admission",
        tenant_id=_required_text(tenant_id, "tenant_id"),
        project_group_id=group_id,
        client_id=client_id,
        idempotency_key=f"evidence-admission:{bundle['bundle_id']}",
        request_hash=value_hash(
            {
                "command": "admit_evidence",
                "payload": {
                    "evidence_bundle": bundle,
                    "timesheet_draft": timesheet,
                },
            }
        ),
        endpoint=(
            f"/api/internal/project-groups/{group_id}/"
            "governed-work/evidence-admissions"
        ),
        payload={"evidence_bundle": bundle, "timesheet_draft": timesheet},
        created_at=created_at,
    )


def _validate_retirement(
    raw: Mapping[str, Any],
    *,
    key_provider: Any,
) -> Dict[str, Any]:
    retirement = _expect_exact_keys(
        dict(raw),
        _RETIREMENT_KEYS,
        "outbox retirement",
    )
    if retirement["schema"] != OUTBOX_RETIREMENT_SCHEMA:
        raise GovernedWorkError("invalid outbox retirement schema")
    _matches(retirement["retirement_id"], _RETIREMENT_ID_RE, "retirement_id")
    _matches(retirement["entry_id"], _ENTRY_ID_RE, "entry_id")
    _matches(retirement["entry_hash"], _HASH64_RE, "entry_hash")
    if retirement["reason_code"] != "superseded_by_acknowledged_request":
        raise GovernedWorkError("invalid outbox retirement reason")
    _matches(
        retirement["superseded_by_entry_id"],
        _ENTRY_ID_RE,
        "superseded_by_entry_id",
    )
    _matches(
        retirement["superseded_by_entry_hash"],
        _HASH64_RE,
        "superseded_by_entry_hash",
    )
    _timestamp(retirement["retired_at"], "retired_at")
    key_id = signer_key_id(key_provider)
    verify_document_signature(
        retirement,
        {key_id: key_provider.get_public_key()},
    )
    return retirement


def _find_outbox_entry(
    target_dir: str,
    entry_id: str,
) -> tuple[Path, Dict[str, Any]]:
    expected = _matches(entry_id, _ENTRY_ID_RE, "entry_id")
    matches = [
        (path, entry)
        for path, entry in _iter_outbox(target_dir)
        if entry.get("entry_id") == expected
    ]
    if len(matches) != 1:
        raise GovernedWorkError("outbox entry was not found uniquely")
    return matches[0]


def _retirement_is_active(
    target_dir: str,
    entry_path: Path,
    entry: Mapping[str, Any],
    *,
    key_provider: Any = None,
) -> bool:
    marker_path = _retirement_path(entry_path)
    if not marker_path.is_file():
        return False
    provider = _local_signing_provider(target_dir, key_provider)
    retirement = _validate_retirement(
        _read_document(marker_path),
        key_provider=provider,
    )
    if (
        retirement["entry_id"] != entry["entry_id"]
        or retirement["entry_hash"] != document_hash(entry)
    ):
        raise GovernedWorkError("outbox retirement does not match its entry")

    replacement_path, replacement = _find_outbox_entry(
        target_dir,
        retirement["superseded_by_entry_id"],
    )
    if retirement["superseded_by_entry_hash"] != document_hash(replacement):
        raise GovernedWorkError("outbox retirement replacement hash mismatch")
    for field in ("command", "tenant_id", "project_group_id"):
        if entry.get(field) != replacement.get(field):
            raise GovernedWorkError(
                f"outbox retirement replacement scope mismatch: {field}"
            )

    replacement_ack_path = _ack_path(replacement_path)
    if not replacement_ack_path.is_file():
        raise GovernedWorkError(
            "outbox retirement replacement acknowledgement is missing"
        )
    replacement_ack = _read_document(replacement_ack_path)
    if (
        replacement_ack.get("entry_id") != replacement["entry_id"]
        or replacement_ack.get("request_hash") != replacement["request_hash"]
    ):
        raise GovernedWorkError(
            "outbox retirement replacement acknowledgement is invalid"
        )
    return True


def retire_outbox_entry(
    target_dir: str,
    *,
    entry_id: str,
    superseded_by_entry_id: str,
    retired_at: Optional[str] = None,
    key_provider: Any = None,
) -> Dict[str, Any]:
    """Stop retrying one immutable request after its replacement is acknowledged."""
    path, entry = _find_outbox_entry(target_dir, entry_id)
    replacement_path, replacement = _find_outbox_entry(
        target_dir,
        superseded_by_entry_id,
    )
    if path == replacement_path:
        raise GovernedWorkError("outbox entry cannot supersede itself")
    if _ack_path(path).exists():
        raise GovernedWorkError("acknowledged outbox entry cannot be retired")
    replacement_ack_path = _ack_path(replacement_path)
    if not replacement_ack_path.is_file():
        raise GovernedWorkError("replacement outbox entry is not acknowledged")
    replacement_ack = _read_document(replacement_ack_path)
    if (
        replacement_ack.get("entry_id") != replacement["entry_id"]
        or replacement_ack.get("request_hash") != replacement["request_hash"]
    ):
        raise GovernedWorkError("replacement acknowledgement does not match its entry")
    for field in ("command", "tenant_id", "project_group_id"):
        if entry.get(field) != replacement.get(field):
            raise GovernedWorkError(
                f"replacement outbox entry scope mismatch: {field}"
            )

    provider = _local_signing_provider(target_dir, key_provider)
    seed = {
        "entry_id": entry["entry_id"],
        "entry_hash": document_hash(entry),
        "reason_code": "superseded_by_acknowledged_request",
        "superseded_by_entry_id": replacement["entry_id"],
        "superseded_by_entry_hash": document_hash(replacement),
    }
    retirement_id = "apgwor_" + value_hash(seed).split(":", 1)[1][:32]
    marker_path = _retirement_path(path)
    if marker_path.is_file():
        existing = _validate_retirement(
            _read_document(marker_path),
            key_provider=provider,
        )
        for field, expected in {
            "retirement_id": retirement_id,
            **seed,
        }.items():
            if existing[field] != expected:
                raise GovernedWorkError(
                    f"outbox retirement conflicts on {field}"
                )
        return {
            "ok": True,
            "stored": False,
            "retirement_id": retirement_id,
            "retirement_hash": document_hash(existing),
            "entry_id": entry["entry_id"],
            "superseded_by_entry_id": replacement["entry_id"],
            "pending": False,
        }

    body = {
        "schema": OUTBOX_RETIREMENT_SCHEMA,
        "retirement_id": retirement_id,
        **seed,
        "retired_at": retired_at or _utc_now(),
    }
    signed = sign_document(
        body,
        provider,
        key_id=signer_key_id(provider),
    )
    _validate_retirement(signed, key_provider=provider)
    _immutable_write(marker_path, signed)
    return {
        "ok": True,
        "stored": True,
        "retirement_id": retirement_id,
        "retirement_hash": document_hash(signed),
        "entry_id": entry["entry_id"],
        "superseded_by_entry_id": replacement["entry_id"],
        "pending": False,
    }


def _iter_outbox(target_dir: str) -> Iterable[tuple[Path, Dict[str, Any]]]:
    directory = governed_work_root(target_dir) / "outbox"
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith((".ack.json", ".retired.json")):
            continue
        entry = _read_document(path)
        _expect_exact_keys(entry, _OUTBOX_KEYS, "outbox entry")
        if entry["schema"] != OUTBOX_SCHEMA:
            raise GovernedWorkError("invalid outbox entry schema")
        yield path, entry


def pending_count(
    target_dir: str = ".",
    *,
    key_provider: Any = None,
) -> int:
    pending = 0
    for path, entry in _iter_outbox(target_dir):
        if _ack_path(path).exists():
            continue
        if _retirement_is_active(
            target_dir,
            path,
            entry,
            key_provider=key_provider,
        ):
            continue
        pending += 1
    return pending


def validate_admission_receipt(
    document: Mapping[str, Any],
    *,
    trusted_authority_keys: Mapping[str, str | bytes],
) -> Dict[str, Any]:
    receipt = _expect_exact_keys(dict(document), _RECEIPT_KEYS, "admission receipt")
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise GovernedWorkError("invalid admission receipt schema")
    _matches(receipt["receipt_id"], _RECEIPT_ID_RE, "receipt_id")
    _required_text(receipt["tenant_id"], "tenant_id")
    _matches(receipt["project_group_id"], _GROUP_ID_RE, "project_group_id")
    _matches(receipt["request_hash"], _HASH64_RE, "request_hash")
    if receipt["command"] != "evidence_admission":
        raise GovernedWorkError("receipt command must be evidence_admission")
    _matches(receipt["resource_id"], _BUNDLE_ID_RE, "resource_id")
    _matches(receipt["resource_hash"], _HASH64_RE, "resource_hash")
    _integer(receipt["projection_cursor"], "projection_cursor")
    _timestamp(receipt["accepted_at"], "accepted_at")
    _expect_exact_keys(receipt["signature"], _SIGNATURE_KEYS, "signature")
    assert_privacy_safe(receipt)
    from apatch.governed_work import (
        PLATFORM_EVIDENCE_ADMISSION_PURPOSE,
        verify_platform_document_signature,
    )

    verify_platform_document_signature(
        receipt,
        trusted_authority_keys,
        purpose=PLATFORM_EVIDENCE_ADMISSION_PURPOSE,
    )
    return receipt


def _response_json(response: Any) -> Dict[str, Any]:
    try:
        body = response.json()
    except Exception as exc:
        raise GovernedWorkError("Platform returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise GovernedWorkError("Platform response must be an object")
    return body


def _ack_source_binding(
    target_dir: str,
    entry: Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    trusted_authority_keys: Mapping[str, str | bytes],
) -> Dict[str, Any]:
    binding = dict(body)
    change = entry["payload"]["change"]
    result = store_project_source_binding(
        target_dir,
        binding,
        change=change,
        trusted_authority_keys=trusted_authority_keys,
        status=None,
    )
    return {
        "schema": "apatch.governed-work-ack.v1",
        "entry_id": entry["entry_id"],
        "request_hash": entry["request_hash"],
        "command": entry["command"],
        "resource_id": result["binding_id"],
        "resource_hash": result["binding_hash"],
        "projection_cursor": 0,
        "acknowledged_at": _utc_now(),
    }


def _ack_evidence(
    entry: Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    trusted_authority_keys: Mapping[str, str | bytes],
) -> Dict[str, Any]:
    validated = validate_admission_receipt(
        body, trusted_authority_keys=trusted_authority_keys
    )
    bundle = entry["payload"]["evidence_bundle"]
    if validated["request_hash"] != entry["request_hash"]:
        raise GovernedWorkError("evidence receipt request_hash mismatch")
    if (
        validated["tenant_id"] != entry["tenant_id"]
        or validated["project_group_id"] != entry["project_group_id"]
    ):
        raise GovernedWorkError("evidence receipt authority scope mismatch")
    if validated["resource_id"] != bundle["bundle_id"]:
        raise GovernedWorkError("evidence receipt resource_id mismatch")
    if validated["resource_hash"] != document_hash(bundle):
        raise GovernedWorkError("evidence receipt resource_hash mismatch")
    return {
        "schema": "apatch.governed-work-ack.v1",
        "entry_id": entry["entry_id"],
        "request_hash": entry["request_hash"],
        "command": entry["command"],
        "resource_id": validated["resource_id"],
        "resource_hash": validated["resource_hash"],
        "projection_cursor": validated["projection_cursor"],
        "platform_receipt_hash": document_hash(validated),
        "acknowledged_at": _utc_now(),
    }


def sync_governed_work(
    target_dir: str = ".",
    *,
    platform_url: Optional[str] = None,
    config: Optional[Mapping[str, Any]] = None,
    http_client: Any = None,
    timeout: float = 20.0,
    request_key_provider: Any = None,
) -> Dict[str, Any]:
    cfg = load_config(target_dir, override=config)
    base_url = (platform_url or cfg["platform_url"]).rstrip("/")
    pending = [
        (path, entry)
        for path, entry in _iter_outbox(target_dir)
        if not _ack_path(path).exists()
        and not _retirement_is_active(
            target_dir,
            path,
            entry,
            key_provider=request_key_provider,
        )
    ]
    if not base_url:
        return {
            "ok": True,
            "status": "queued_offline",
            "delivered": 0,
            "pending": len(pending),
            "errors": [],
        }
    if pending:
        try:
            identity = T.service_request_identity(
                target_dir, key_provider=request_key_provider
            )
            if identity["service_request_key_id"] != cfg["service_request_key_id"]:
                raise GovernedWorkError(
                    "configured service request key does not match "
                    "the enrolled APatch identity"
                )
        except GovernedWorkError as exc:
            return {
                "ok": False,
                "status": "credential_unavailable",
                "delivered": 0,
                "pending": len(pending),
                "errors": [
                    {
                        "code": "SIGNED_SERVICE_IDENTITY_UNAVAILABLE",
                        "detail": str(exc),
                    }
                ],
            }
    if http_client is None and httpx is None:
        return {
            "ok": False,
            "status": "transport_unavailable",
            "delivered": 0,
            "pending": len(pending),
            "errors": [{"code": "HTTPX_UNAVAILABLE"}],
        }
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    delivered = 0
    errors = []
    try:
        for path, entry in pending:
            request_body = {
                "subject": entry["client_id"],
                "tenant_id": entry["tenant_id"],
                **entry["payload"],
                "idempotency_key": entry["idempotency_key"],
            }
            raw_body = canonical_bytes(request_body)
            headers = {
                "Content-Type": "application/json",
                "Idempotency-Key": entry["idempotency_key"],
                "X-Apatch-Client-Id": entry["client_id"],
                **T.signed_service_headers(
                    target_dir,
                    method="POST",
                    path=entry["endpoint"],
                    raw_body=raw_body,
                    tenant_id=entry["tenant_id"],
                    subject=entry["client_id"],
                    expected_key_id=cfg["service_request_key_id"],
                    key_provider=request_key_provider,
                ),
            }
            try:
                response = client.post(
                    base_url + entry["endpoint"],
                    content=raw_body,
                    headers=headers,
                )
                if int(response.status_code) not in {200, 201}:
                    errors.append(
                        {
                            "entry_id": entry["entry_id"],
                            "code": "PLATFORM_REJECTED",
                            "status_code": int(response.status_code),
                        }
                    )
                    continue
                body = _response_json(response)
                if entry["command"] == "source_binding":
                    ack = _ack_source_binding(
                        target_dir,
                        entry,
                        body,
                        trusted_authority_keys=cfg["binding_authority_keys"],
                    )
                elif entry["command"] == "evidence_admission":
                    ack = _ack_evidence(
                        entry,
                        body,
                        trusted_authority_keys=cfg["binding_authority_keys"],
                    )
                else:
                    raise GovernedWorkError("unknown governed-work outbox command")
                _atomic_json(_ack_path(path), ack)
                delivered += 1
            except GovernedWorkError as exc:
                errors.append(
                    {
                        "entry_id": entry["entry_id"],
                        "code": "ACK_VALIDATION_FAILED",
                        "detail": str(exc),
                    }
                )
            except Exception:
                errors.append(
                    {
                        "entry_id": entry["entry_id"],
                        "code": "DELIVERY_FAILED",
                    }
                )
    finally:
        if close:
            client.close()
    remaining = pending_count(
        target_dir,
        key_provider=request_key_provider,
    )
    return {
        "ok": not errors,
        "status": "in_sync" if not errors and remaining == 0 else "delivery_incomplete",
        "delivered": delivered,
        "pending": remaining,
        "errors": errors,
    }


def validate_status_projection(document: Mapping[str, Any]) -> Dict[str, Any]:
    status = _expect_exact_keys(dict(document), _STATUS_KEYS, "governed-work status")
    if status["schema"] != "trustchain.governed-work-status.v1":
        raise GovernedWorkError("invalid governed-work status schema")
    _required_text(status["tenant_id"], "tenant_id")
    _matches(status["project_group_id"], _GROUP_ID_RE, "project_group_id")
    _required_text(status["work_program_id"], "work_program_id")
    if status["work_release_id"] is not None:
        _required_text(status["work_release_id"], "work_release_id")
    allowed = {
        "collective_acceptance": {
            "draft",
            "submitted",
            "accepted",
            "rejected",
            "superseded",
        },
        "source_verified": {"unbound", "verified", "revoked"},
        "contribution_bound": {"unbound", "bound", "revoked", "invalid"},
        "timesheet_accepted": {
            "not_submitted",
            "accepted",
            "rejected",
            "revoked",
            "invalid",
        },
    }
    for field, values in allowed.items():
        if status[field] not in values:
            raise GovernedWorkError(f"invalid {field}")
    _integer(status["authority_version"], "authority_version")
    _integer(status["revocation_version"], "revocation_version")
    _integer(status["projection_cursor"], "projection_cursor")
    assert_privacy_safe(status)
    return status


def governed_work_delivery_status(
    target_dir: str = ".",
    *,
    key_provider: Any = None,
) -> Dict[str, Any]:
    delivered = 0
    pending = 0
    max_cursor = 0
    for path, entry in _iter_outbox(target_dir):
        ack_path = _ack_path(path)
        if ack_path.exists():
            ack = _read_document(ack_path)
            delivered += 1
            max_cursor = max(max_cursor, int(ack.get("projection_cursor") or 0))
        elif not _retirement_is_active(
            target_dir,
            path,
            entry,
            key_provider=key_provider,
        ):
            pending += 1
    return {
        "ok": True,
        "schema": "apatch.governed-work-delivery-status.v1",
        "queued": delivered + pending,
        "delivered": delivered,
        "pending": pending,
        "projection_cursor": max_cursor,
    }
