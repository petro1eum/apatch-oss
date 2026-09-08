"""Pull and durably store owner-authorized Avatar taxonomy decisions."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

try:
    from avatar_contract import TaxonomyDecision, TaxonomyDecisionError
except ImportError:  # pragma: no cover - explicit optional-contract mode
    TaxonomyDecision = None  # type: ignore

    class TaxonomyDecisionError(ValueError):
        pass


def default_taxonomy_store_dir() -> str:
    override = os.environ.get("APATCH_AVATAR_TAXONOMY_STORE", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".trustchain", "avatar_taxonomy")


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
            if isinstance(raw, dict) and raw.get("kind") == "taxonomy_decision":
                yield raw


def trusted_taxonomy_public_keys() -> list[str]:
    raw = os.environ.get("APATCH_TRUSTED_TAXONOMY_PUBLIC_KEYS", "")
    if raw.strip():
        return [value.strip() for value in raw.split(",") if value.strip()]
    from apatch.avatar_runtime_config import (
        AvatarRuntimeConfigError,
        load_avatar_trust_policy,
    )

    try:
        return load_avatar_trust_policy().get("taxonomy_public_keys", [])
    except AvatarRuntimeConfigError as exc:
        raise TaxonomyDecisionError(str(exc)) from None


def _pull_trusted_keys(
    explicit: Optional[list[str]],
) -> Optional[list[str]]:
    keys = (
        [value.strip() for value in explicit if value.strip()]
        if explicit is not None
        else trusted_taxonomy_public_keys()
    )
    if os.environ.get("TC_ENVIRONMENT", "").lower() == "production" and not keys:
        raise TaxonomyDecisionError(
            "production taxonomy ingest requires APATCH_TRUSTED_TAXONOMY_PUBLIC_KEYS"
        )
    return keys or None


def _issuer_unconfigured(exc: Exception) -> Dict[str, Any]:
    return {
        "ok": False,
        "status": "issuer_unconfigured",
        "received": 0,
        "stored": 0,
        "duplicates": 0,
        "errors": [{"error": str(exc)}],
    }


def validate_taxonomy_decision(
    raw: Dict[str, Any],
    *,
    trusted_public_keys: Optional[list[str]] = None,
) -> TaxonomyDecision:
    if TaxonomyDecision is None:
        raise TaxonomyDecisionError(
            "avatar-contract is required to consume taxonomy decisions"
        )
    keys = (
        trusted_public_keys
        if trusted_public_keys is not None
        else trusted_taxonomy_public_keys()
    )
    if not keys:
        if os.environ.get("TC_ENVIRONMENT", "").lower() == "production":
            raise TaxonomyDecisionError(
                "production taxonomy ingest requires APATCH_TRUSTED_TAXONOMY_PUBLIC_KEYS"
            )
        embedded = str((raw.get("trustchain_audit") or {}).get("public_key") or "")
        keys = [embedded] if embedded else []
    for key in keys:
        try:
            return TaxonomyDecision.from_wire(
                raw,
                verify_signature=True,
                trusted_public_key=key,
            )
        except TaxonomyDecisionError:
            continue
    raise TaxonomyDecisionError(
        "TaxonomyDecision is not signed by a trusted HC Tracker issuer"
    )


def store_taxonomy_decision(
    raw: Dict[str, Any],
    *,
    store_dir: Optional[str] = None,
    trusted_public_keys: Optional[list[str]] = None,
) -> Dict[str, Any]:
    parsed = validate_taxonomy_decision(
        raw,
        trusted_public_keys=trusted_public_keys,
    )
    root = store_dir or default_taxonomy_store_dir()
    path = os.path.join(
        root,
        parsed.subject_avatar_id,
        f"{parsed.decision_id}.json",
    )
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
        if existing != raw:
            raise TaxonomyDecisionError(
                "decision_id is already bound to another payload"
            )
        return {"stored": False, "path": path, "decision_id": parsed.decision_id}
    _atomic_json(path, raw)
    return {"stored": True, "path": path, "decision_id": parsed.decision_id}


def load_taxonomy_index(
    *,
    avatar_id: Optional[str] = None,
    store_dir: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return latest valid decision per source event, honoring supersede links."""
    root = store_dir or default_taxonomy_store_dir()
    valid: list[tuple[Any, Dict[str, Any]]] = []
    for raw in _iter_wire(root):
        if avatar_id and raw.get("subject_avatar_id") != avatar_id:
            continue
        try:
            parsed = validate_taxonomy_decision(raw)
        except TaxonomyDecisionError:
            continue
        valid.append((parsed, raw))
    valid.sort(key=lambda item: (item[0].issued_at, item[0].decision_id))
    index: Dict[str, Dict[str, Any]] = {}
    for parsed, raw in valid:
        current = index.get(parsed.source_event_id)
        supersedes = parsed.decision.get("supersedes_decision_id")
        if current is None:
            if supersedes is not None:
                continue
        elif supersedes != current.get("decision_id"):
            continue
        index[parsed.source_event_id] = raw
    return index


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return base + "/internal/avatar-taxonomy-decisions"
    return base + "/api/v1/internal/avatar-taxonomy-decisions"


def _store_pulled(
    body: Dict[str, Any],
    *,
    avatar_id: str,
    store_dir: Optional[str],
    trusted_public_keys: Optional[list[str]],
) -> Dict[str, Any]:
    raw_items = body.get("decisions") if isinstance(body.get("decisions"), list) else []
    stored = 0
    duplicates = 0
    errors = []
    for raw in raw_items:
        try:
            if not isinstance(raw, dict) or raw.get("subject_avatar_id") != avatar_id:
                raise TaxonomyDecisionError("TaxonomyDecision subject does not match owner")
            result = store_taxonomy_decision(
                raw,
                store_dir=store_dir,
                trusted_public_keys=trusted_public_keys,
            )
            stored += int(result["stored"])
            duplicates += int(not result["stored"])
        except (TaxonomyDecisionError, OSError, ValueError) as exc:
            errors.append({
                "decision_id": raw.get("decision_id") if isinstance(raw, dict) else None,
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


def pull_taxonomy_decisions(
    *,
    base_url: str,
    avatar_id: str,
    service_token: str = "",
    store_dir: Optional[str] = None,
    timeout: float = 10.0,
    http_client=None,
    trusted_public_keys: Optional[list[str]] = None,
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
    try:
        pull_keys = _pull_trusted_keys(trusted_public_keys)
    except TaxonomyDecisionError as exc:
        return _issuer_unconfigured(exc)
    if http_client is None and httpx is None:
        return {
            "ok": False,
            "status": "transport_unavailable",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": "httpx is not installed"}],
        }
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    try:
        response = client.get(
            _endpoint(base_url),
            params={"avatar_id": avatar_id, "limit": 1000},
            headers={"X-Service-Token": service_token} if service_token else {},
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
            "errors": [{
                "status_code": response.status_code,
                "detail": body.get("detail") if isinstance(body, dict) else None,
            }],
        }
    return _store_pulled(
        body,
        avatar_id=avatar_id,
        store_dir=store_dir,
        trusted_public_keys=pull_keys,
    )


def pull_taxonomy_decisions_from_platform(
    *,
    platform_url: str,
    avatar_token: str,
    avatar_id: str,
    store_dir: Optional[str] = None,
    timeout: float = 10.0,
    http_client=None,
    trusted_public_keys: Optional[list[str]] = None,
) -> Dict[str, Any]:
    if not platform_url or not avatar_token:
        return {
            "ok": False,
            "status": "platform_misconfigured",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": "APATCH_PLATFORM_URL and APATCH_AVATAR_TOKEN are required"}],
        }
    try:
        pull_keys = _pull_trusted_keys(trusted_public_keys)
    except TaxonomyDecisionError as exc:
        return _issuer_unconfigured(exc)
    if http_client is None and httpx is None:
        return {
            "ok": False,
            "status": "transport_unavailable",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": "httpx is not installed"}],
        }
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    try:
        response = client.get(
            platform_url.rstrip("/") + "/api/avatar/taxonomy-decisions",
            headers={"Authorization": f"Bearer {avatar_token}"},
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
    return _store_pulled(
        body,
        avatar_id=avatar_id,
        store_dir=store_dir,
        trusted_public_keys=pull_keys,
    )
