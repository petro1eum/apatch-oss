"""Durable CapabilityEvidence delivery from apatch to HC Tracker.

Every governed attestation first writes a signed metadata distillate to a local
outbox. If Tracker is configured, pending bundles are then delivered over the
internal HTTP contract. Network failure never loses evidence and retry is safe because
the bundle is content-addressed and Tracker is idempotent.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from importlib import metadata
from typing import Any, Dict, Iterable, Optional, Tuple

try:
    import httpx
except ImportError:  # pragma: no cover - exercised by the explicit client path
    httpx = None  # type: ignore


def default_evidence_outbox_dir() -> str:
    override = os.environ.get("APATCH_AVATAR_EVIDENCE_OUTBOX", "").strip()
    if override:
        return override
    return os.path.join(
        os.path.expanduser("~"), ".trustchain", "avatar_evidence_outbox"
    )


def tracker_config_from_env() -> Dict[str, Any]:
    from apatch.avatar_runtime_config import load_avatar_sync_config

    durable = load_avatar_sync_config()
    return {
        "base_url": (
            os.environ.get("APATCH_HC_TRACKER_URL", "").strip()
            or str(durable.get("tracker_base_url") or "")
        ),
        "service_token": os.environ.get("APATCH_HC_SERVICE_TOKEN", "").strip(),
        "platform_url": os.environ.get("APATCH_PLATFORM_URL", "").strip(),
        "avatar_token": os.environ.get("APATCH_AVATAR_TOKEN", "").strip(),
    }


AVATAR_RUNTIME_REQUIRED_SYMBOLS = (
    "CapabilityEvidenceBundle",
    "ContributionEvent",
    "OUTCOME_ACCEPTANCE_ROLES",
    "OutcomeAttestation",
    "TaxonomyDecision",
    "assert_outcome_acceptance_authority",
    "build_work_review_package",
)


def avatar_runtime_compatibility() -> Dict[str, Any]:
    """Report the exact optional Avatar runtime visible to this interpreter."""
    try:
        installed_version = metadata.version("avatar-contract")
    except metadata.PackageNotFoundError:
        installed_version = None

    base = {
        "dependency": "avatar-contract",
        "installed_version": installed_version,
        "python_executable": os.path.abspath(sys.executable),
        "python_executable_realpath": os.path.realpath(sys.executable),
        "isolated_python": bool(sys.flags.isolated),
        "required_symbols": list(AVATAR_RUNTIME_REQUIRED_SYMBOLS),
    }
    try:
        import avatar_contract
    except ImportError:
        return {
            **base,
            "ok": False,
            "status": "dependency_incompatible",
            "module_path": None,
            "missing_symbols": list(AVATAR_RUNTIME_REQUIRED_SYMBOLS),
            "action_required": ["install_apatch_avatar_extra"],
            "restart_required": True,
        }

    module_file = getattr(avatar_contract, "__file__", None)
    missing = [
        name
        for name in AVATAR_RUNTIME_REQUIRED_SYMBOLS
        if not hasattr(avatar_contract, name)
    ]
    return {
        **base,
        "ok": not missing,
        "status": "ready" if not missing else "dependency_incompatible",
        "module_path": os.path.realpath(module_file) if module_file else None,
        "missing_symbols": missing,
        "action_required": [] if not missing else ["upgrade_avatar_contract"],
        "restart_required": bool(missing),
    }


def _with_operational_status(
    result: Dict[str, Any],
    *,
    configured: bool,
) -> Dict[str, Any]:
    """Distinguish durable local success from completed cross-system sync."""
    evidence_delivery = result.get("evidence", {}).get("delivery", {})
    contribution_delivery = result.get("contributions", {}).get("delivery", {})
    pending = int(evidence_delivery.get("pending") or 0) + int(
        contribution_delivery.get("pending") or 0
    )
    inbound_ready = all(
        result.get(name, {}).get("status") == "pulled"
        for name in ("taxonomy", "outcomes")
    )
    delivery_ready = pending == 0 and all(
        delivery.get("ok") is not False
        for delivery in (evidence_delivery, contribution_delivery)
    )
    complete = bool(
        configured
        and result.get("ok")
        and inbound_ready
        and delivery_ready
    )

    action_required: list[str] = []
    if not configured:
        status = "tracker_unconfigured"
        action_required.append("configure_tracker_or_trustchain_avatar")
    elif not result.get("ok"):
        status = "delivery_incomplete"
        action_required.append("repair_and_retry")
    elif pending:
        status = "delivery_pending"
        action_required.append("retry_pending_outboxes")
    elif not inbound_ready:
        status = "inbound_sync_incomplete"
        action_required.append("repair_inbound_sync")
    else:
        status = "synchronized"

    result.update({
        "complete": complete,
        "status": status,
        "pending_outbox_items": pending,
        "action_required": action_required,
    })
    return result


def _atomic_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _ack_path(payload_path: str) -> str:
    return payload_path[:-5] + ".ack.json"


def _iter_payloads(outbox_dir: str) -> Iterable[Tuple[str, Dict[str, Any]]]:
    if not os.path.isdir(outbox_dir):
        return
    for root, _dirs, files in os.walk(outbox_dir):
        for name in sorted(files):
            if not name.endswith(".json") or name.endswith(".ack.json"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("bundle_id"):
                yield path, payload


def _semantic_fingerprint(payload: Dict[str, Any]) -> str:
    import hashlib

    stable = {
        key: value for key, value in payload.items()
        if key not in {"bundle_id", "generated_at", "signature"}
    }
    encoded = json.dumps(
        stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _compilation_summary(bundle: Dict[str, Any]) -> Dict[str, Any]:
    episodes = [
        item for item in bundle.get("episodes") or [] if isinstance(item, dict)
    ]
    capabilities = [
        item
        for item in bundle.get("capability_estimates") or []
        if isinstance(item, dict)
    ]

    def refs(item: Dict[str, Any]) -> list[str]:
        return [
            str(value)
            for value in (item.get("task") or {}).get("taxonomy_refs") or []
            if value
        ]

    def detailed(item: Dict[str, Any]) -> bool:
        values = refs(item)
        return bool(
            any(value.startswith("soc:") for value in values)
            and any(value.startswith("onet-task:") for value in values)
            and any(value.startswith("onet-skill:") for value in values)
        )

    classified = [item for item in episodes if detailed(item)]
    accepted = [
        item for item in episodes
        if (item.get("outcome") or {}).get("basis")
        in {"external_acceptance", "work_asset_reuse"}
    ]
    review_ready = [
        item for item in episodes
        if (item.get("review_package") or {}).get("status") == "ready"
    ]
    awaiting_acceptance = [
        item for item in review_ready
        if (item.get("outcome") or {}).get("basis")
        not in {"external_acceptance", "work_asset_reuse"}
    ]
    classified_without_review = [
        item for item in classified
        if (item.get("review_package") or {}).get("status") != "ready"
    ]
    market_capabilities = [
        item for item in capabilities
        if any(
            str(value).startswith(("soc:", "onet-task:", "onet-skill:", "cbm:"))
            for value in item.get("taxonomy_refs") or []
        )
    ]
    next_actions: list[Dict[str, Any]] = []
    if classified_without_review:
        next_actions.append({
            "code": "prepare_public_review_package",
            "affected_count": len(classified_without_review),
        })
    if awaiting_acceptance:
        next_actions.append({
            "code": "request_natural_counterparty_acceptance",
            "affected_count": len(awaiting_acceptance),
        })
    if capabilities and not market_capabilities:
        next_actions.append({
            "code": "classify_accepted_work_with_market_taxonomy",
            "affected_count": len(capabilities),
        })
    return {
        "source_event_count": int(
            (bundle.get("evidence_scope") or {}).get("event_count") or 0
        ),
        "exported_episode_count": len(episodes),
        "detailed_taxonomy_episode_count": len(classified),
        "counterparty_accepted_episode_count": len(accepted),
        "review_ready_episode_count": len(review_ready),
        "eligible_episode_count": sum(
            bool(item.get("eligible_for_capability")) for item in episodes
        ),
        "capability_count": len(capabilities),
        "market_taxonomy_capability_count": len(market_capabilities),
        "next_actions": next_actions,
    }


def queue_current_evidence(
    target_dir: str = ".",
    *,
    outbox_dir: Optional[str] = None,
    outcome_store_dir: Optional[str] = None,
    taxonomy_store_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Build and durably queue the current signed evidence snapshot."""
    from apatch.avatar_evidence import build_evidence_bundle

    outbox = outbox_dir or default_evidence_outbox_dir()
    bundle = build_evidence_bundle(
        target_dir,
        outcome_store_dir=outcome_store_dir,
        taxonomy_store_dir=taxonomy_store_dir,
    )
    fingerprint = _semantic_fingerprint(bundle)
    for path, existing in _iter_payloads(outbox):
        if _semantic_fingerprint(existing) == fingerprint:
            return {
                "ok": True,
                "queued": False,
                "duplicate_snapshot": True,
                "bundle_id": existing["bundle_id"],
                "avatar_id": existing.get("avatar_id"),
                "path": path,
                "acknowledged": os.path.exists(_ack_path(path)),
                "compilation": _compilation_summary(existing),
            }

    avatar_id = str(bundle.get("avatar_id") or "unbound")
    path = os.path.join(outbox, avatar_id, f"{bundle['bundle_id']}.json")
    _atomic_json(path, bundle)
    return {
        "ok": True,
        "queued": True,
        "duplicate_snapshot": False,
        "bundle_id": bundle["bundle_id"],
        "avatar_id": bundle.get("avatar_id"),
        "path": path,
        "acknowledged": False,
        "compilation": _compilation_summary(bundle),
    }


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return base + "/internal/capability-evidence"
    return base + "/api/v1/internal/capability-evidence"


def pending_evidence_count(outbox_dir: Optional[str] = None) -> int:
    outbox = outbox_dir or default_evidence_outbox_dir()
    return sum(
        not os.path.exists(_ack_path(path))
        for path, _payload in _iter_payloads(outbox)
    )


def deliver_pending_evidence(
    *,
    base_url: str,
    service_token: str = "",
    outbox_dir: Optional[str] = None,
    timeout: float = 10.0,
    http_client=None,
) -> Dict[str, Any]:
    """Deliver every unacknowledged bundle and persist local ACK receipts."""
    outbox = outbox_dir or default_evidence_outbox_dir()
    endpoint = _endpoint(base_url)
    pending = [
        (path, payload) for path, payload in _iter_payloads(outbox)
        if not os.path.exists(_ack_path(path))
    ]
    if not base_url:
        return {
            "ok": True,
            "status": "queued_offline",
            "delivered": 0,
            "pending": len(pending),
            "errors": [],
        }
    if http_client is None and httpx is None:
        return {
            "ok": False,
            "status": "transport_unavailable",
            "delivered": 0,
            "pending": len(pending),
            "errors": [{"error": "httpx is not installed"}],
        }

    headers = {"X-Service-Token": service_token} if service_token else {}
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    delivered = 0
    errors = []
    try:
        for path, payload in pending:
            try:
                response = client.post(endpoint, json=payload, headers=headers)
                body = response.json()
                accepted = response.status_code in {200, 201} and body.get("accepted") is True
            except Exception as exc:
                errors.append({
                    "bundle_id": payload.get("bundle_id"),
                    "error": str(exc),
                })
                continue
            if not accepted:
                errors.append({
                    "bundle_id": payload.get("bundle_id"),
                    "status_code": response.status_code,
                    "detail": body.get("detail") if isinstance(body, dict) else None,
                })
                continue
            _atomic_json(_ack_path(path), {
                "bundle_id": payload["bundle_id"],
                "avatar_id": payload.get("avatar_id"),
                "accepted_at": datetime.now(timezone.utc).isoformat(),
                "endpoint": endpoint,
                "status_code": response.status_code,
                "stored": bool(body.get("stored")),
                "duplicate": bool(body.get("duplicate")),
            })
            delivered += 1
    finally:
        if close:
            client.close()

    remaining = pending_evidence_count(outbox)
    return {
        "ok": not errors,
        "status": "delivered" if not errors else "delivery_incomplete",
        "delivered": delivered,
        "pending": remaining,
        "errors": errors,
    }


def deliver_pending_evidence_to_platform(
    *,
    platform_url: str,
    avatar_token: str,
    outbox_dir: Optional[str] = None,
    timeout: float = 10.0,
    http_client=None,
) -> Dict[str, Any]:
    """Deliver evidence through the owner-authenticated TrustChain Avatar BFF."""
    outbox = outbox_dir or default_evidence_outbox_dir()
    endpoint = platform_url.rstrip("/") + "/api/avatar/evidence/upload"
    pending = [
        (path, payload) for path, payload in _iter_payloads(outbox)
        if not os.path.exists(_ack_path(path))
    ]
    if not platform_url or not avatar_token:
        return {
            "ok": False,
            "status": "platform_misconfigured",
            "delivered": 0,
            "pending": len(pending),
            "errors": [{"error": "APATCH_PLATFORM_URL and APATCH_AVATAR_TOKEN are required"}],
        }
    if http_client is None and httpx is None:
        return {
            "ok": False,
            "status": "transport_unavailable",
            "delivered": 0,
            "pending": len(pending),
            "errors": [{"error": "httpx is not installed"}],
        }

    headers = {"Authorization": f"Bearer {avatar_token}"}
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    delivered = 0
    errors = []
    try:
        for path, payload in pending:
            try:
                response = client.post(
                    endpoint,
                    json={"evidence": payload},
                    headers=headers,
                )
                body = response.json()
                accepted = (
                    response.status_code == 200
                    and isinstance(body, dict)
                    and body.get("accepted") is True
                    and body.get("signature_verified") is True
                )
            except Exception as exc:
                errors.append({
                    "bundle_id": payload.get("bundle_id"),
                    "error": str(exc),
                })
                continue
            if not accepted:
                errors.append({
                    "bundle_id": payload.get("bundle_id"),
                    "status_code": response.status_code,
                    "detail": body.get("detail") if isinstance(body, dict) else None,
                    "status": body.get("status") if isinstance(body, dict) else None,
                })
                continue
            _atomic_json(_ack_path(path), {
                "bundle_id": payload["bundle_id"],
                "avatar_id": payload.get("avatar_id"),
                "accepted_at": datetime.now(timezone.utc).isoformat(),
                "endpoint": endpoint,
                "status_code": response.status_code,
                "stored": bool(body.get("stored")),
                "duplicate": bool(body.get("duplicate")),
                "signature_verified": True,
            })
            delivered += 1
    finally:
        if close:
            client.close()

    return {
        "ok": not errors,
        "status": "delivered" if not errors else "delivery_incomplete",
        "delivered": delivered,
        "pending": pending_evidence_count(outbox),
        "errors": errors,
    }


def sync_avatar_evidence(
    target_dir: str = ".",
    *,
    base_url: Optional[str] = None,
    service_token: Optional[str] = None,
    outbox_dir: Optional[str] = None,
    outcome_store_dir: Optional[str] = None,
    taxonomy_store_dir: Optional[str] = None,
    platform_url: Optional[str] = None,
    avatar_token: Optional[str] = None,
    timeout: float = 10.0,
    http_client=None,
) -> Dict[str, Any]:
    """Queue the current distillate, then deliver all pending snapshots if configured."""
    config = tracker_config_from_env()
    queue = queue_current_evidence(
        target_dir,
        outbox_dir=outbox_dir,
        outcome_store_dir=outcome_store_dir,
        taxonomy_store_dir=taxonomy_store_dir,
    )
    resolved_platform = (
        platform_url if platform_url is not None else config["platform_url"]
    )
    resolved_avatar_token = (
        avatar_token if avatar_token is not None else config["avatar_token"]
    )
    if resolved_platform or resolved_avatar_token:
        delivery = deliver_pending_evidence_to_platform(
            platform_url=resolved_platform,
            avatar_token=resolved_avatar_token,
            outbox_dir=outbox_dir,
            timeout=timeout,
            http_client=http_client,
        )
    else:
        delivery = deliver_pending_evidence(
            base_url=base_url if base_url is not None else config["base_url"],
            service_token=(
                service_token if service_token is not None else config["service_token"]
            ),
            outbox_dir=outbox_dir,
            timeout=timeout,
            http_client=http_client,
        )
    return {
        "ok": bool(queue.get("ok")) and bool(delivery.get("ok")),
        "queue": queue,
        "delivery": delivery,
    }


def sync_avatar_state(
    target_dir: str = ".",
    *,
    base_url: Optional[str] = None,
    service_token: Optional[str] = None,
    evidence_outbox_dir: Optional[str] = None,
    contribution_outbox_dir: Optional[str] = None,
    outcome_store_dir: Optional[str] = None,
    taxonomy_store_dir: Optional[str] = None,
    timeout: float = 20.0,
    http_client=None,
) -> Dict[str, Any]:
    """Synchronize owner decisions in, then timeline and evidence out."""
    runtime = avatar_runtime_compatibility()
    if not runtime["ok"]:
        return {
            **runtime,
            "retryable": False,
            "outbox_preserved": True,
        }
    from apatch.contribution_delivery import sync_contributions
    from apatch.contribution import resolve_identity
    from apatch.outcome_delivery import (
        pull_outcome_attestations,
        pull_outcome_attestations_from_platform,
    )
    from apatch.taxonomy_delivery import (
        pull_taxonomy_decisions,
        pull_taxonomy_decisions_from_platform,
    )

    from apatch.avatar_runtime_config import AvatarRuntimeConfigError

    try:
        config = tracker_config_from_env()
    except AvatarRuntimeConfigError as exc:
        return {
            "ok": False,
            "status": "configuration_invalid",
            "error_code": str(exc),
            "retryable": False,
            "outbox_preserved": True,
        }
    platform_url = config["platform_url"]
    avatar_token = config["avatar_token"]
    if platform_url or avatar_token:
        from apatch.contribution_export import sync_to_trustchain_avatar
        from apatch.contribution import resolve_identity

        avatar_id = str(resolve_identity(target_dir).get("key_id") or "")
        taxonomy = pull_taxonomy_decisions_from_platform(
            platform_url=platform_url,
            avatar_token=avatar_token,
            avatar_id=avatar_id,
            store_dir=taxonomy_store_dir,
            timeout=timeout,
            http_client=http_client,
        ) if avatar_id else {
            "ok": False,
            "status": "identity_unbound",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": "Avatar identity is not configured"}],
        }
        outcomes = pull_outcome_attestations_from_platform(
            platform_url=platform_url,
            avatar_token=avatar_token,
            avatar_id=avatar_id,
            store_dir=outcome_store_dir,
            timeout=timeout,
            http_client=http_client,
        ) if avatar_id else {
            "ok": False,
            "status": "identity_unbound",
            "received": 0,
            "stored": 0,
            "duplicates": 0,
            "errors": [{"error": "Avatar identity is not configured"}],
        }
        contributions = sync_to_trustchain_avatar(
            platform_url=platform_url,
            token=avatar_token,
            avatar_id=avatar_id or None,
            timeout=timeout,
            http_client=http_client,
        )
        evidence = sync_avatar_evidence(
            target_dir,
            outbox_dir=evidence_outbox_dir,
            outcome_store_dir=outcome_store_dir,
            taxonomy_store_dir=taxonomy_store_dir,
            platform_url=platform_url,
            avatar_token=avatar_token,
            timeout=timeout,
            http_client=http_client,
        )
        result = {
            "ok": (
                bool(outcomes.get("ok"))
                and bool(taxonomy.get("ok"))
                and bool(evidence.get("ok"))
                and bool(contributions.get("ok"))
            ),
            "transport": "trustchain_avatar",
            "taxonomy": taxonomy,
            "outcomes": outcomes,
            "evidence": evidence,
            "contributions": {
                "ok": contributions.get("ok"),
                "exported": 0,
                "delivery": {
                    "ok": contributions.get("ok"),
                    "status": (
                        "delivered_with_quarantine"
                        if contributions.get("ok")
                        and contributions.get("quarantined_total")
                        else (
                            "delivered"
                            if contributions.get("ok")
                            else "delivery_incomplete"
                        )
                    ),
                    "delivered": contributions.get("accepted", 0),
                    "pending": int(contributions.get("pending", 0)),
                    "attempted": int(contributions.get("attempted", 0)),
                    "quarantined": int(contributions.get("quarantined", 0)),
                    "quarantined_total": int(
                        contributions.get("quarantined_total", 0)
                    ),
                    "errors": contributions.get("errors", []),
                },
            },
        }
        return _with_operational_status(
            result,
            configured=bool(platform_url and avatar_token),
        )
    resolved_url = base_url if base_url is not None else config["base_url"]
    from apatch.service_secret import (
        ServiceSecretResolutionError,
        resolve_hc_tracker_token,
    )

    try:
        resolved_token = resolve_hc_tracker_token(
            service_token if service_token is not None else config["service_token"]
        )
    except ServiceSecretResolutionError as exc:
        return {
            "ok": False,
            "status": "credential_unavailable",
            "error_code": str(exc),
            "retryable": True,
            "outbox_preserved": True,
        }
    avatar_id = str(resolve_identity(target_dir).get("key_id") or "")
    taxonomy = pull_taxonomy_decisions(
        base_url=resolved_url,
        avatar_id=avatar_id,
        service_token=resolved_token,
        store_dir=taxonomy_store_dir,
        timeout=timeout,
        http_client=http_client,
    ) if avatar_id else {
        "ok": False,
        "status": "identity_unbound",
        "received": 0,
        "stored": 0,
        "duplicates": 0,
        "errors": [{"error": "Avatar identity is not configured"}],
    }
    outcomes = pull_outcome_attestations(
        base_url=resolved_url,
        avatar_id=avatar_id,
        service_token=resolved_token,
        store_dir=outcome_store_dir,
        timeout=timeout,
        http_client=http_client,
    ) if avatar_id else {
        "ok": False,
        "status": "identity_unbound",
        "received": 0,
        "stored": 0,
        "duplicates": 0,
        "errors": [{"error": "Avatar identity is not configured"}],
    }
    evidence = sync_avatar_evidence(
        target_dir,
        base_url=resolved_url,
        service_token=resolved_token,
        outbox_dir=evidence_outbox_dir,
        outcome_store_dir=outcome_store_dir,
        taxonomy_store_dir=taxonomy_store_dir,
        timeout=timeout,
        http_client=http_client,
    )
    # The evidence ACK establishes the subject public key used to authenticate legacy
    # ContributionEvent receipts, so contribution delivery must run second.
    contributions = sync_contributions(
        base_url=resolved_url,
        service_token=resolved_token,
        outbox_dir=contribution_outbox_dir,
        timeout=timeout,
        avatar_id=evidence.get("queue", {}).get("avatar_id"),
        http_client=http_client,
    )
    result = {
        "ok": (
            bool(outcomes.get("ok"))
            and bool(taxonomy.get("ok"))
            and bool(evidence.get("ok"))
            and bool(contributions.get("ok"))
        ),
        "transport": "hc_tracker_internal",
        "taxonomy": taxonomy,
        "outcomes": outcomes,
        "evidence": evidence,
        "contributions": contributions,
    }
    return _with_operational_status(result, configured=bool(resolved_url))
