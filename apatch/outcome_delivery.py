"""Pull and durably store independently signed WorkEpisode outcomes."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

try:
    from avatar_contract import (
        OUTCOME_ACCEPTANCE_ROLES,
        OutcomeAttestation,
        OutcomeAttestationError,
        assert_outcome_acceptance_authority,
    )
except ImportError:  # pragma: no cover - explicit optional-contract mode
    OutcomeAttestation = None  # type: ignore
    OUTCOME_ACCEPTANCE_ROLES = frozenset()  # type: ignore

    class OutcomeAttestationError(ValueError):
        pass

    def assert_outcome_acceptance_authority(*_args, **_kwargs):  # type: ignore
        raise OutcomeAttestationError(
            "avatar-contract is required to authorize external outcomes"
        )


def default_outcome_store_dir() -> str:
    override = os.environ.get("APATCH_AVATAR_OUTCOME_STORE", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".trustchain", "avatar_outcomes")


def _atomic_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _iter_wire(store_dir: str) -> Iterable[Dict[str, Any]]:
    if not os.path.isdir(store_dir):
        return
    for root, _dirs, files in os.walk(store_dir):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    raw = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict) and raw.get("kind") == "outcome_attestation":
                yield raw


def trusted_outcome_issuers() -> list[Dict[str, Any]]:
    """Read the purpose-bound counterparty registry used for capability input."""
    raw = os.environ.get("APATCH_TRUSTED_OUTCOME_ISSUERS", "").strip()
    if raw:
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OutcomeAttestationError(
                "APATCH_TRUSTED_OUTCOME_ISSUERS must be valid JSON"
            ) from exc
    else:
        from apatch.avatar_runtime_config import (
            AvatarRuntimeConfigError,
            load_avatar_trust_policy,
        )

        try:
            values = load_avatar_trust_policy().get("outcome_issuers", [])
        except AvatarRuntimeConfigError as exc:
            raise OutcomeAttestationError(str(exc)) from None
    if not isinstance(values, list):
        raise OutcomeAttestationError(
            "APATCH_TRUSTED_OUTCOME_ISSUERS must be a JSON array"
        )
    issuers: list[Dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise OutcomeAttestationError("outcome issuer entry must be an object")
        public_key = str(value.get("public_key") or "").strip()
        organization_id = str(value.get("organization_id") or "").strip()
        roles = {
            str(role).strip()
            for role in value.get("roles") or []
            if str(role).strip()
        }
        if not public_key or not organization_id or not roles:
            raise OutcomeAttestationError(
                "outcome issuer requires public_key, organization_id and roles"
            )
        unknown = roles - set(OUTCOME_ACCEPTANCE_ROLES)
        if unknown:
            raise OutcomeAttestationError(
                f"outcome issuer contains roles that cannot accept work: {sorted(unknown)}"
            )
        issuers.append(
            {
                "public_key": public_key,
                "organization_id": organization_id,
                "roles": roles,
            }
        )
    return issuers


def validate_outcome_attestation(
    raw: Dict[str, Any],
    *,
    trusted_issuers: Optional[list[Dict[str, Any]]] = None,
) -> OutcomeAttestation:
    if OutcomeAttestation is None:
        raise OutcomeAttestationError(
            "avatar-contract is required to consume external outcomes"
        )
    issuers = (
        trusted_issuers
        if trusted_issuers is not None
        else trusted_outcome_issuers()
    )
    if not issuers:
        raise OutcomeAttestationError(
            "outcome ingest requires a pinned outcome issuer registry"
        )
    authority_error: Optional[OutcomeAttestationError] = None
    for issuer in issuers:
        try:
            parsed = OutcomeAttestation.from_wire(
                raw,
                verify_signature=True,
                trusted_public_key=str(issuer.get("public_key") or ""),
            )
        except OutcomeAttestationError:
            continue
        try:
            assert_outcome_acceptance_authority(
                parsed.verifier,
                expected_organization_id=str(issuer.get("organization_id") or ""),
                authorized_roles=set(issuer.get("roles") or []),
            )
        except OutcomeAttestationError as exc:
            authority_error = exc
            continue
        return parsed
    if authority_error is not None:
        raise authority_error
    raise OutcomeAttestationError(
        "OutcomeAttestation is not signed by an authorized work counterparty"
    )


def store_outcome_attestation(
    raw: Dict[str, Any],
    *,
    store_dir: Optional[str] = None,
    trusted_issuers: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    parsed = validate_outcome_attestation(
        raw,
        trusted_issuers=trusted_issuers,
    )
    root = store_dir or default_outcome_store_dir()
    path = os.path.join(
        root,
        parsed.subject_avatar_id,
        f"{parsed.attestation_id}.json",
    )
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
        if existing != raw:
            raise OutcomeAttestationError(
                "attestation_id is already bound to another payload"
            )
        return {"stored": False, "path": path, "attestation_id": parsed.attestation_id}
    _atomic_json(path, raw)
    return {"stored": True, "path": path, "attestation_id": parsed.attestation_id}


def load_outcome_index(
    *,
    avatar_id: Optional[str] = None,
    store_dir: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    root = store_dir or default_outcome_store_dir()
    index: Dict[str, Dict[str, Any]] = {}
    for raw in _iter_wire(root):
        if avatar_id and raw.get("subject_avatar_id") != avatar_id:
            continue
        try:
            parsed = validate_outcome_attestation(raw)
        except OutcomeAttestationError:
            continue
        episode_id = parsed.work_episode_id
        if episode_id:
            index[episode_id] = raw
    return index


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return base + "/internal/avatar-outcomes"
    return base + "/api/v1/internal/avatar-outcomes"


def _store_pulled_attestations(
    body: Dict[str, Any],
    *,
    avatar_id: str,
    store_dir: Optional[str],
    trusted_issuers: Optional[list[Dict[str, Any]]],
) -> Dict[str, Any]:
    raw_items = (
        body.get("attestations")
        if isinstance(body.get("attestations"), list)
        else []
    )
    stored = 0
    duplicates = 0
    errors = []
    for raw in raw_items:
        try:
            result = store_outcome_attestation(
                raw,
                store_dir=store_dir,
                trusted_issuers=trusted_issuers,
            )
            stored += int(result["stored"])
            duplicates += int(not result["stored"])
        except (OutcomeAttestationError, OSError, ValueError) as exc:
            errors.append({
                "attestation_id": (
                    raw.get("attestation_id") if isinstance(raw, dict) else None
                ),
                "error": str(exc),
            })
    return {
        "ok": not errors,
        "status": "pulled" if not errors else "delivery_incomplete",
        "avatar_id": avatar_id,
        "received": len(raw_items),
        "stored": stored,
        "duplicates": duplicates,
        "errors": errors,
    }


def pull_outcome_attestations(
    *,
    base_url: str,
    avatar_id: str,
    service_token: str = "",
    store_dir: Optional[str] = None,
    timeout: float = 10.0,
    http_client=None,
    trusted_issuers: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not base_url:
        return {
            "ok": True,
            "status": "tracker_unconfigured",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [],
        }
    if http_client is None and httpx is None:
        return {
            "ok": False,
            "status": "transport_unavailable",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": "httpx is not installed"}],
        }
    headers = {"X-Service-Token": service_token} if service_token else {}
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    try:
        response = client.get(
            _endpoint(base_url),
            params={"avatar_id": avatar_id, "limit": 250},
            headers=headers,
        )
        body = response.json()
    except Exception as exc:
        return {
            "ok": False,
            "status": "delivery_incomplete",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": str(exc)}],
        }
    finally:
        if close:
            client.close()
    if response.status_code != 200 or body.get("avatar_id") != avatar_id:
        return {
            "ok": False,
            "status": "tracker_rejected",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"status_code": response.status_code, "detail": body.get("detail")}],
        }
    return _store_pulled_attestations(
        body,
        avatar_id=avatar_id,
        store_dir=store_dir,
        trusted_issuers=trusted_issuers,
    )


def pull_outcome_attestations_from_platform(
    *,
    platform_url: str,
    avatar_token: str,
    avatar_id: str,
    store_dir: Optional[str] = None,
    timeout: float = 10.0,
    http_client=None,
    trusted_issuers: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Pull owner-scoped outcomes through the TrustChain Avatar BFF."""
    if not platform_url or not avatar_token:
        return {
            "ok": False,
            "status": "platform_misconfigured",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{
                "error": "APATCH_PLATFORM_URL and APATCH_AVATAR_TOKEN are required"
            }],
        }
    if http_client is None and httpx is None:
        return {
            "ok": False,
            "status": "transport_unavailable",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": "httpx is not installed"}],
        }

    endpoint = platform_url.rstrip("/") + "/api/avatar/outcomes"
    headers = {"Authorization": f"Bearer {avatar_token}"}
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    try:
        response = client.get(endpoint, headers=headers)
        body = response.json()
    except Exception as exc:
        return {
            "ok": False,
            "status": "delivery_incomplete",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": str(exc)}],
        }
    finally:
        if close:
            client.close()
    if (
        response.status_code != 200
        or not isinstance(body, dict)
        or body.get("status") != "ready"
        or body.get("avatar_id") != avatar_id
    ):
        return {
            "ok": False,
            "status": "platform_rejected",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{
                "status_code": response.status_code,
                "detail": body.get("detail") if isinstance(body, dict) else None,
                "status": body.get("status") if isinstance(body, dict) else None,
            }],
        }
    return _store_pulled_attestations(
        body,
        avatar_id=avatar_id,
        store_dir=store_dir,
        trusted_issuers=trusted_issuers,
    )
