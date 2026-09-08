"""Durable ContributionEvent HTTP delivery to HC Tracker."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

from apatch.contribution_export import default_outbox_dir, export_pending


def _atomic_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _ack_path(path: str) -> str:
    return path[:-5] + ".ack.json"


def _event_avatar_id(event: Dict[str, Any]) -> str:
    identity = event.get("identity") or {}
    identity_key = identity.get("key_id") if isinstance(identity, dict) else None
    return str(event.get("avatar_id") or identity_key or "")


def _iter_events(outbox_dir: str) -> Iterable[Tuple[str, Dict[str, Any]]]:
    if not os.path.isdir(outbox_dir):
        return
    for root, _dirs, files in os.walk(outbox_dir):
        for name in sorted(files):
            if not name.endswith(".json") or name.endswith(".ack.json"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    event = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(event, dict) and event.get("event_id"):
                yield path, event


def pending_contribution_count(
    outbox_dir: Optional[str] = None,
    *,
    avatar_id: Optional[str] = None,
) -> int:
    outbox = outbox_dir or default_outbox_dir()
    return sum(
        not os.path.exists(_ack_path(path))
        for path, event in _iter_events(outbox)
        if not avatar_id or _event_avatar_id(event) == str(avatar_id)
    )


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return base + "/internal/contribution-events/batch"
    return base + "/api/v1/internal/contribution-events/batch"


def _reconcile_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return base + "/internal/contribution-events/reconcile"
    return base + "/api/v1/internal/contribution-events/reconcile"


def reconcile_contributions(
    *,
    base_url: str,
    service_token: str = "",
    avatar_id: str,
    event_ids: Iterable[str],
    timeout: float = 20.0,
    batch_size: int = 1_000,
    http_client=None,
) -> Dict[str, Any]:
    """Compare the durable local event set with one Tracker Avatar identity."""
    ids = list(dict.fromkeys(str(value) for value in event_ids if value))
    result: Dict[str, Any] = {
        "ok": False,
        "status": "reconciliation_failed",
        "requested_count": len(ids),
        "present_count": 0,
        "verified_present_count": 0,
        "unverified_present_count": 0,
        "missing_count": 0,
        "missing_envelope_count": 0,
        "conflict_count": 0,
        "present_event_ids": [],
        "verified_present_event_ids": [],
        "unverified_present_event_ids": [],
        "missing_event_ids": [],
        "missing_envelope_event_ids": [],
        "conflict_event_ids": [],
        "verification_classified": True,
        "storage_complete": False,
        "envelope_complete": False,
        "verification_complete": False,
        "complete": False,
        "errors": [],
    }
    if not ids:
        result.update({
            "ok": True,
            "status": "in_sync",
            "storage_complete": True,
            "envelope_complete": True,
            "verification_complete": True,
            "complete": True,
        })
        return result
    if not base_url:
        result.update({"ok": True, "status": "queued_offline", "complete": False})
        return result
    if not avatar_id:
        result["status"] = "identity_required"
        result["errors"].append({"error": "avatar_id is required for reconciliation"})
        result["complete"] = False
        return result
    if http_client is None and httpx is None:
        result["status"] = "transport_unavailable"
        result["errors"].append({"error": "httpx is not installed"})
        result["complete"] = False
        return result

    size = min(5_000, max(1, int(batch_size)))
    headers = {"X-Service-Token": service_token} if service_token else {}
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    try:
        for offset in range(0, len(ids), size):
            chunk = ids[offset:offset + size]
            try:
                response = client.post(
                    _reconcile_endpoint(base_url),
                    json={"avatar_id": avatar_id, "event_ids": chunk},
                    headers=headers,
                )
                body = response.json()
            except Exception as exc:
                result["errors"].append({"offset": offset, "error": str(exc)})
                continue
            if response.status_code != 200 or not isinstance(body, dict):
                result["errors"].append({
                    "offset": offset,
                    "status_code": response.status_code,
                    "detail": body.get("detail") if isinstance(body, dict) else None,
                })
                continue

            present = [str(value) for value in body.get("present_event_ids") or []]
            missing = [str(value) for value in body.get("missing_event_ids") or []]
            conflicts = [
                str(value) for value in body.get("conflict_event_ids") or []
            ]
            missing_envelopes = [
                str(value)
                for value in body.get("missing_envelope_event_ids") or []
            ]
            classified = present + missing + conflicts
            if (
                str(body.get("avatar_id") or "") != avatar_id
                or len(classified) != len(set(classified))
                or set(classified) != set(chunk)
            ):
                result["errors"].append({
                    "offset": offset,
                    "error": "Tracker returned an inconsistent reconciliation ACK",
                })
                continue
            if (
                len(missing_envelopes) != len(set(missing_envelopes))
                or not set(missing_envelopes).issubset(set(present))
            ):
                result["errors"].append({
                    "offset": offset,
                    "error": "Tracker returned an inconsistent envelope partition",
                })
                continue

            has_verified = "verified_present_event_ids" in body
            has_unverified = "unverified_present_event_ids" in body
            if has_verified or has_unverified:
                verified = [
                    str(value)
                    for value in body.get("verified_present_event_ids") or []
                ]
                unverified = [
                    str(value)
                    for value in body.get("unverified_present_event_ids") or []
                ]
                verification_partition = verified + unverified
                if (
                    not (has_verified and has_unverified)
                    or len(verification_partition)
                    != len(set(verification_partition))
                    or set(verification_partition) != set(present)
                ):
                    result["errors"].append({
                        "offset": offset,
                        "error": (
                            "Tracker returned an inconsistent verification "
                            "partition"
                        ),
                    })
                    continue
                result["verified_present_event_ids"].extend(verified)
                result["unverified_present_event_ids"].extend(unverified)
            else:
                # Older Tracker versions only classified storage presence.
                result["verification_classified"] = False
            result["present_event_ids"].extend(present)
            result["missing_event_ids"].extend(missing)
            result["missing_envelope_event_ids"].extend(missing_envelopes)
            result["conflict_event_ids"].extend(conflicts)
    finally:
        if close:
            client.close()

    result["present_count"] = len(result["present_event_ids"])
    result["verified_present_count"] = len(
        result["verified_present_event_ids"]
    )
    result["unverified_present_count"] = len(
        result["unverified_present_event_ids"]
    )
    result["missing_count"] = len(result["missing_event_ids"])
    result["missing_envelope_count"] = len(
        result["missing_envelope_event_ids"]
    )
    result["conflict_count"] = len(result["conflict_event_ids"])
    result["ok"] = not result["errors"]
    result["storage_complete"] = (
        result["ok"]
        and result["missing_count"] == 0
        and result["conflict_count"] == 0
    )
    result["envelope_complete"] = (
        result["ok"] and result["missing_envelope_count"] == 0
    )
    result["verification_complete"] = (
        not result["verification_classified"]
        or result["unverified_present_count"] == 0
    )
    result["complete"] = (
        result["storage_complete"]
        and result["envelope_complete"]
        and result["verification_complete"]
    )
    if result["errors"]:
        result["status"] = "reconciliation_failed"
    elif result["conflict_count"]:
        result["status"] = "identity_conflict"
    elif result["missing_count"]:
        result["status"] = "repair_required"
    elif not result["envelope_complete"]:
        result["status"] = "envelope_repair_required"
    elif not result["verification_complete"]:
        result["status"] = "verification_required"
    else:
        result["status"] = "in_sync"
    return result


def deliver_pending_contributions(
    *,
    base_url: str,
    service_token: str = "",
    outbox_dir: Optional[str] = None,
    timeout: float = 20.0,
    batch_size: int = 200,
    avatar_id: Optional[str] = None,
    force_event_ids: Optional[Iterable[str]] = None,
    http_client=None,
) -> Dict[str, Any]:
    """Deliver pending events in bounded batches and ACK accepted rows only."""
    outbox = outbox_dir or default_outbox_dir()
    forced = {str(value) for value in (force_event_ids or []) if value}
    pending = [
        (path, event) for path, event in _iter_events(outbox)
        if (
            not os.path.exists(_ack_path(path))
            or str(event.get("event_id") or "") in forced
        )
        and (not avatar_id or _event_avatar_id(event) == str(avatar_id))
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

    size = min(250, max(1, int(batch_size)))
    headers = {"X-Service-Token": service_token} if service_token else {}
    client = http_client or httpx.Client(timeout=timeout)
    close = http_client is None
    delivered = 0
    delivered_forced: set[str] = set()
    errors: list[dict[str, Any]] = []
    try:
        for offset in range(0, len(pending), size):
            chunk = pending[offset:offset + size]
            try:
                response = client.post(
                    _endpoint(base_url),
                    json=[event for _path, event in chunk],
                    headers=headers,
                )
                body = response.json()
            except Exception as exc:
                errors.append({"offset": offset, "error": str(exc)})
                continue
            if response.status_code != 200 or not isinstance(body, dict):
                errors.append({
                    "offset": offset,
                    "status_code": response.status_code,
                    "detail": body.get("detail") if isinstance(body, dict) else None,
                })
                continue
            by_event_id = {
                str(item.get("event_id")): item
                for item in body.get("results") or []
                if isinstance(item, dict) and item.get("event_id")
            }
            for path, event in chunk:
                event_id = str(event["event_id"])
                result = by_event_id.get(event_id)
                if not result or result.get("accepted") is not True:
                    errors.append({
                        "event_id": event_id,
                        "detail": (result or {}).get("detail") or "missing batch ACK",
                    })
                    continue
                prior_acknowledged = os.path.exists(_ack_path(path))
                _atomic_json(_ack_path(path), {
                    "event_id": event_id,
                    "avatar_id": _event_avatar_id(event) or None,
                    "accepted_at": datetime.now(timezone.utc).isoformat(),
                    "endpoint": _endpoint(base_url),
                    "stored": bool(result.get("stored")),
                    "duplicate": bool(result.get("duplicate")),
                    "signature_verified": bool(result.get("signature_verified")),
                    "reconciled_replay": event_id in forced,
                    "prior_acknowledged": prior_acknowledged,
                })
                delivered += 1
                if event_id in forced:
                    delivered_forced.add(event_id)
    finally:
        if close:
            client.close()

    matching_ids = {
        str(event.get("event_id") or "")
        for _path, event in _iter_events(outbox)
        if not avatar_id or _event_avatar_id(event) == str(avatar_id)
    }
    normal_pending_ids = {
        str(event.get("event_id") or "")
        for path, event in _iter_events(outbox)
        if not os.path.exists(_ack_path(path))
        and (not avatar_id or _event_avatar_id(event) == str(avatar_id))
    }
    forced_pending_ids = (forced & matching_ids) - delivered_forced
    remaining = len(normal_pending_ids | forced_pending_ids)
    reason_counts = Counter(
        str(item.get("detail") or item.get("error") or item.get("status_code") or "unknown")
        for item in errors
    )
    return {
        "ok": not errors,
        "status": "delivered" if not errors else "delivery_incomplete",
        "delivered": delivered,
        "reconciled_delivered": len(delivered_forced),
        "reconciled_pending": len(forced_pending_ids),
        "pending": remaining,
        "error_count": len(errors),
        "error_reasons": dict(reason_counts.most_common(10)),
        "errors": errors[:20],
    }


def sync_contributions(
    *,
    base_url: str,
    service_token: str = "",
    store_dir: Optional[str] = None,
    outbox_dir: Optional[str] = None,
    timeout: float = 20.0,
    avatar_id: Optional[str] = None,
    http_client=None,
) -> Dict[str, Any]:
    outbox = outbox_dir or default_outbox_dir()
    exported = export_pending(
        store_dir=store_dir,
        outbox_dir=outbox,
        avatar_id=avatar_id,
    )
    local_event_ids = [
        str(event.get("event_id"))
        for _path, event in _iter_events(outbox)
        if (
            event.get("event_id")
            and (
                not avatar_id
                or _event_avatar_id(event) == str(avatar_id)
            )
        )
    ]
    reconciliation_before = reconcile_contributions(
        base_url=base_url,
        service_token=service_token,
        avatar_id=str(avatar_id or ""),
        event_ids=local_event_ids,
        timeout=timeout,
        http_client=http_client,
    )
    missing_event_ids = set(
        reconciliation_before.get("missing_event_ids") or []
        if reconciliation_before.get("ok")
        else []
    )
    unverified_event_ids = set(
        reconciliation_before.get("unverified_present_event_ids") or []
        if (
            reconciliation_before.get("ok")
            and reconciliation_before.get("verification_classified")
        )
        else []
    )
    missing_envelope_event_ids = set(
        reconciliation_before.get("missing_envelope_event_ids") or []
        if reconciliation_before.get("ok")
        else []
    )
    replay_event_ids = (
        missing_event_ids
        | unverified_event_ids
        | missing_envelope_event_ids
    )
    delivery = deliver_pending_contributions(
        base_url=base_url,
        service_token=service_token,
        outbox_dir=outbox,
        timeout=timeout,
        avatar_id=avatar_id,
        force_event_ids=replay_event_ids,
        http_client=http_client,
    )
    should_verify_repair = (
        bool(reconciliation_before.get("ok"))
        and reconciliation_before.get("status") != "queued_offline"
    )
    reconciliation_after = (
        reconcile_contributions(
            base_url=base_url,
            service_token=service_token,
            avatar_id=str(avatar_id or ""),
            event_ids=local_event_ids,
            timeout=timeout,
            http_client=http_client,
        )
        if should_verify_repair
        else reconciliation_before
    )

    after_ok = bool(reconciliation_after.get("ok"))
    remaining_missing_ids = set(
        reconciliation_after.get("missing_event_ids") or []
        if after_ok
        else missing_event_ids
    )
    verification_classified = bool(
        reconciliation_before.get("verification_classified")
        and reconciliation_after.get("verification_classified")
    )
    remaining_unverified_ids = set(
        reconciliation_after.get("unverified_present_event_ids") or []
        if after_ok and verification_classified
        else unverified_event_ids
    )
    remaining_missing_envelope_ids = set(
        reconciliation_after.get("missing_envelope_event_ids") or []
        if after_ok
        else missing_envelope_event_ids
    )
    present_after = set(reconciliation_after.get("present_event_ids") or [])
    verified_after = set(
        reconciliation_after.get("verified_present_event_ids") or []
    )
    repaired_ids = missing_event_ids & present_after if after_ok else set()
    reverified_ids = (
        unverified_event_ids & verified_after
        if after_ok and verification_classified
        else set()
    )
    repaired_envelope_ids = (
        missing_envelope_event_ids - remaining_missing_envelope_ids
        if after_ok else set()
    )
    conflict_ids = list(
        reconciliation_after.get("conflict_event_ids") or []
    )
    storage_complete = bool(reconciliation_after.get("storage_complete"))
    envelope_complete = bool(reconciliation_after.get("envelope_complete"))
    verification_complete = bool(
        reconciliation_after.get("verification_complete")
    )
    reconciliation_complete = bool(reconciliation_after.get("complete"))

    if reconciliation_before.get("status") == "queued_offline":
        reconciliation_status = "queued_offline"
    elif not reconciliation_before.get("ok") or not after_ok:
        reconciliation_status = "reconciliation_failed"
    elif conflict_ids:
        reconciliation_status = "identity_conflict"
    elif remaining_missing_ids:
        reconciliation_status = "repair_incomplete"
    elif remaining_missing_envelope_ids:
        reconciliation_status = "envelope_repair_incomplete"
    elif remaining_unverified_ids:
        reconciliation_status = "verification_incomplete"
    elif missing_event_ids and unverified_event_ids:
        reconciliation_status = "repaired_and_reverified"
    elif missing_envelope_event_ids:
        reconciliation_status = "envelopes_repaired"
    elif missing_event_ids:
        reconciliation_status = "repaired"
    elif unverified_event_ids:
        reconciliation_status = "reverified"
    else:
        reconciliation_status = "in_sync"

    errors = list(reconciliation_before.get("errors") or [])
    if reconciliation_after is not reconciliation_before:
        errors.extend(reconciliation_after.get("errors") or [])
    reconciliation = {
        "ok": bool(reconciliation_before.get("ok")) and after_ok,
        "status": reconciliation_status,
        "complete": reconciliation_complete,
        "storage_complete": storage_complete,
        "envelope_complete": envelope_complete,
        "verification_complete": verification_complete,
        "verification_classified": verification_classified,
        "requested_count": int(
            reconciliation_after.get("requested_count") or 0
        ),
        "present_count": int(
            reconciliation_after.get("present_count") or 0
        ),
        "verified_present_count": int(
            reconciliation_after.get("verified_present_count") or 0
        ),
        "unverified_present_count": int(
            reconciliation_after.get("unverified_present_count") or 0
        ),
        "missing_before_repair": len(missing_event_ids),
        "unverified_before_repair": len(unverified_event_ids),
        "missing_envelopes_before_repair": len(missing_envelope_event_ids),
        "repaired_count": len(repaired_ids),
        "reverified_count": len(reverified_ids),
        "repaired_envelope_count": len(repaired_envelope_ids),
        "remaining_missing_count": len(remaining_missing_ids),
        "remaining_unverified_count": len(remaining_unverified_ids),
        "remaining_missing_envelope_count": len(
            remaining_missing_envelope_ids
        ),
        "conflict_count": len(conflict_ids),
        "conflict_event_ids": conflict_ids[:20],
        "errors": errors[:20],
    }
    sync_ok = bool(delivery["ok"])
    if reconciliation_status != "queued_offline":
        sync_ok = sync_ok and reconciliation_complete
    return {
        "ok": sync_ok,
        "exported": len(exported),
        "reconciliation": reconciliation,
        "delivery": delivery,
    }
