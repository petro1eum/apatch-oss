"""Contribution export — the apatch → HC pipe (RFP-028 / SPEC-CONTRIB-PIPE-1).

Durable, idempotent hand-off: signed ContributionEvent receipts from the per-identity
store are copied into an append-only outbox directory that the HC ingest drains. Loss-
and duplicate-free: an event is identified by its stable ``event_id``; re-running
exports nothing already present in the outbox. The optional owner/web sync path keeps
separate delivery receipts; no broker is required for the durable-first outbox path
(RFP-028 §3.4).
"""
from __future__ import annotations

import json
import os
import shutil
import hashlib
from typing import Any, Dict, List, Optional

from apatch.contribution import contribution_store_dir


_PERMANENT_REJECTION_STATUSES = {"event_conflict", "tracker_rejected"}


def default_outbox_dir() -> str:
    """Outbox location (override: APATCH_CONTRIB_OUTBOX)."""
    override = os.environ.get("APATCH_CONTRIB_OUTBOX", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".trustchain", "contributions_outbox")


def iter_store_events(store_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """All signed receipts in the per-identity store."""
    base = store_dir or contribution_store_dir()
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(base):
        return out
    for root, _dirs, files in os.walk(base):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    ev = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(ev, dict) and ev.get("event_id"):
                out.append(ev)
    return out


def _outbox_path(outbox_dir: str, event_id: str) -> str:
    return os.path.join(outbox_dir, f"{event_id}.json")


def export_pending(
    store_dir: Optional[str] = None,
    outbox_dir: Optional[str] = None,
    *,
    avatar_id: Optional[str] = None,
) -> List[str]:
    """Copy not-yet-exported receipts into the outbox. Returns new ``event_id``s.

    Idempotent: an event already present in the outbox (by ``<event_id>.json``) is
    skipped. Writes atomically (tmp + os.replace). Safe to re-run.
    """
    outbox = outbox_dir or default_outbox_dir()
    os.makedirs(outbox, exist_ok=True)
    exported: List[str] = []
    for ev in iter_store_events(store_dir):
        if avatar_id and _event_avatar_id(ev) != str(avatar_id):
            continue
        event_id = str(ev["event_id"])
        dest = _outbox_path(outbox, event_id)
        if os.path.exists(dest):
            continue  # already handed off — no duplicate
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ev, fh, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, dest)
        exported.append(event_id)
    return exported


def outbox_events(outbox_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """All events currently queued in the outbox (what HC ingest will drain)."""
    return iter_store_events(outbox_dir or default_outbox_dir())


def default_sync_receipt_dir() -> str:
    override = os.environ.get("APATCH_AVATAR_SYNC_RECEIPTS", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".trustchain", "avatar_sync_receipts")


def _receipt_path(receipt_dir: str, event_id: str) -> str:
    safe = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return os.path.join(receipt_dir, f"{safe}.json")


def _event_digest(event: Dict[str, Any]) -> str:
    raw = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_avatar_id(event: Dict[str, Any]) -> str:
    identity = event.get("identity")
    identity_key = identity.get("key_id") if isinstance(identity, dict) else None
    return str(event.get("avatar_id") or identity_key or "")


def _read_receipt(
    receipt_dir: str,
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return None
    path = _receipt_path(receipt_dir, event_id)
    try:
        with open(path, encoding="utf-8") as fh:
            receipt = json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if (
        isinstance(receipt, dict)
        and receipt.get("event_id") == event_id
        and receipt.get("source_sha256") == _event_digest(event)
    ):
        return receipt
    return None


def _read_synced(receipt_dir: str, event: Dict[str, Any]) -> bool:
    return _read_receipt(receipt_dir, event) is not None


def _read_quarantined(receipt_dir: str, event: Dict[str, Any]) -> bool:
    receipt = _read_receipt(receipt_dir, event)
    ack = receipt.get("ack") if isinstance(receipt, dict) else None
    return isinstance(ack, dict) and ack.get("disposition") == "quarantined"


def _write_synced(receipt_dir: str, event: Dict[str, Any], ack: Dict[str, Any]) -> None:
    os.makedirs(receipt_dir, exist_ok=True)
    event_id = str(event["event_id"])
    path = _receipt_path(receipt_dir, event_id)
    tmp = path + ".tmp"
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "source_sha256": _event_digest(event),
        "ack": ack,
    }
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, path)


def _reconcile_with_trustchain_avatar(
    *,
    client: Any,
    base_url: str,
    bearer: str,
    avatar_id: str,
    events: List[Dict[str, Any]],
    batch_size: int = 1_000,
) -> Dict[str, Any]:
    event_ids = list(dict.fromkeys(
        str(event.get("event_id") or "") for event in events
        if event.get("event_id")
    ))
    result: Dict[str, Any] = {
        "ok": False,
        "status": "reconciliation_failed",
        "requested_count": len(event_ids),
        "present_event_ids": [],
        "verified_present_event_ids": [],
        "unverified_present_event_ids": [],
        "missing_event_ids": [],
        "conflict_event_ids": [],
        "verification_classified": True,
        "storage_complete": False,
        "verification_complete": False,
        "complete": False,
        "errors": [],
    }
    if not event_ids:
        result.update({
            "ok": True,
            "status": "in_sync",
            "storage_complete": True,
            "verification_complete": True,
            "complete": True,
        })
        return result
    if not avatar_id:
        result["status"] = "identity_required"
        result["errors"].append("Avatar identity is required for reconciliation")
        result["complete"] = False
        return result

    endpoint = base_url.rstrip("/") + "/api/avatar/contributions/reconcile"
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
    }
    size = min(5_000, max(1, int(batch_size)))
    for offset in range(0, len(event_ids), size):
        chunk = event_ids[offset:offset + size]
        try:
            response = client.post(
                endpoint,
                headers=headers,
                json={"avatar_id": avatar_id, "event_ids": chunk},
            )
            body = response.json()
        except Exception as exc:
            result["errors"].append(str(exc))
            continue
        if response.status_code < 200 or response.status_code >= 300:
            result["errors"].append(
                f"TrustChain reconciliation HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
            continue
        if (
            not isinstance(body, dict)
            or body.get("available") is not True
            or body.get("status") != "ready"
        ):
            status = body.get("status") if isinstance(body, dict) else "invalid_response"
            result["remote_status"] = status
            result["errors"].append(
                f"TrustChain reconciliation unavailable: {status}"
            )
            continue
        present = [str(value) for value in body.get("present_event_ids") or []]
        missing = [str(value) for value in body.get("missing_event_ids") or []]
        conflicts = [str(value) for value in body.get("conflict_event_ids") or []]
        classified = present + missing + conflicts
        if (
            str(body.get("avatar_id") or "") != avatar_id
            or len(classified) != len(set(classified))
            or set(classified) != set(chunk)
        ):
            result["errors"].append(
                "TrustChain returned an inconsistent reconciliation ACK"
            )
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
                or len(verification_partition) != len(set(verification_partition))
                or set(verification_partition) != set(present)
            ):
                result["errors"].append(
                    "TrustChain returned an inconsistent verification partition"
                )
                continue
            result["verified_present_event_ids"].extend(verified)
            result["unverified_present_event_ids"].extend(unverified)
        else:
            result["verification_classified"] = False
        result["present_event_ids"].extend(present)
        result["missing_event_ids"].extend(missing)
        result["conflict_event_ids"].extend(conflicts)

    result["present_count"] = len(result["present_event_ids"])
    result["verified_present_count"] = len(
        result["verified_present_event_ids"]
    )
    result["unverified_present_count"] = len(
        result["unverified_present_event_ids"]
    )
    result["missing_count"] = len(result["missing_event_ids"])
    result["conflict_count"] = len(result["conflict_event_ids"])
    result["ok"] = not result["errors"]
    result["storage_complete"] = (
        result["ok"]
        and result["missing_count"] == 0
        and result["conflict_count"] == 0
    )
    result["verification_complete"] = (
        not result["verification_classified"]
        or result["unverified_present_count"] == 0
    )
    result["complete"] = (
        result["storage_complete"] and result["verification_complete"]
    )
    if result["errors"]:
        result["status"] = "reconciliation_failed"
    elif result["conflict_count"]:
        result["status"] = "identity_conflict"
    elif result["missing_count"]:
        result["status"] = "repair_required"
    elif not result["verification_complete"]:
        result["status"] = "verification_required"
    else:
        result["status"] = "in_sync"
    return result


def sync_to_trustchain_avatar(
    *,
    platform_url: Optional[str] = None,
    token: Optional[str] = None,
    store_dir: Optional[str] = None,
    receipt_dir: Optional[str] = None,
    limit: int = 100,
    timeout: float = 15.0,
    dry_run: bool = False,
    avatar_id: Optional[str] = None,
    http_client: Any = None,
) -> Dict[str, Any]:
    """Upload local signed ContributionEvents with an owner-scoped Avatar sync token."""
    base_url = (platform_url or os.environ.get("APATCH_PLATFORM_URL") or "").strip()
    bearer = (token or os.environ.get("APATCH_AVATAR_TOKEN") or "").strip()
    receipts = receipt_dir or default_sync_receipt_dir()
    events = [
        event for event in iter_store_events(store_dir)
        if not avatar_id or _event_avatar_id(event) == str(avatar_id)
    ]
    pending = [event for event in events if not _read_synced(receipts, event)]
    batch_size = max(1, min(int(limit), 100))
    result: Dict[str, Any] = {
        "ok": False,
        "platform_url": base_url or None,
        "store_count": len(events),
        "pending": len(pending),
        "attempted": 0,
        "accepted": 0,
        "duplicates": 0,
        "quarantined": 0,
        "quarantined_total": sum(
            _read_quarantined(receipts, event) for event in events
        ),
        "rejections": [],
        "errors": [],
        "dry_run": dry_run,
    }
    if dry_run:
        result["ok"] = True
        result["would_attempt"] = len(pending)
        result["batch_count"] = (
            (len(pending) + batch_size - 1) // batch_size if pending else 0
        )
        return result
    if not base_url:
        result["errors"].append("APATCH_PLATFORM_URL or --platform-url is required")
        return result
    if not bearer:
        result["errors"].append("APATCH_AVATAR_TOKEN or --token is required")
        return result
    close_client = False
    client = http_client
    if client is None:
        try:
            import httpx
        except ImportError:
            result["errors"].append("httpx is required for avatar sync")
            return result
        client = httpx.Client(timeout=timeout)
        close_client = True

    try:
        reconcilable_events = [
            event for event in events if not _read_quarantined(receipts, event)
        ]
        event_avatar_ids = {
            _event_avatar_id(event) for event in reconcilable_events
            if _event_avatar_id(event)
        }
        resolved_avatar_id = str(avatar_id or "")
        if not resolved_avatar_id and len(event_avatar_ids) == 1:
            resolved_avatar_id = next(iter(event_avatar_ids))
        reconciliation_before = _reconcile_with_trustchain_avatar(
            client=client,
            base_url=base_url,
            bearer=bearer,
            avatar_id=resolved_avatar_id,
            events=reconcilable_events,
        )
        missing_event_ids = set(
            reconciliation_before.get("missing_event_ids") or []
        ) if reconciliation_before.get("ok") else set()
        unverified_event_ids = set(
            reconciliation_before.get("unverified_present_event_ids") or []
        ) if (
            reconciliation_before.get("ok")
            and reconciliation_before.get("verification_classified")
        ) else set()
        replay_event_ids = missing_event_ids | unverified_event_ids
        pending = [
            event for event in events
            if (
                not _read_synced(receipts, event)
                or str(event.get("event_id") or "") in replay_event_ids
            )
            and not _read_quarantined(receipts, event)
        ]
        result["pending"] = len(pending)
        delivered_event_ids: set[str] = set()
        quarantined_event_ids: set[str] = set()
        for offset in range(0, len(pending), batch_size):
            selected = pending[offset : offset + batch_size]
            response = client.post(
                base_url.rstrip("/") + "/api/avatar/contributions/upload",
                headers={
                    "Authorization": f"Bearer {bearer}",
                    "Content-Type": "application/json",
                },
                json={"events": selected},
            )
            result["attempted"] += len(selected)
            if response.status_code < 200 or response.status_code >= 300:
                result["errors"].append(
                    f"TrustChain HTTP {response.status_code}: {response.text[:300]}"
                )
                continue
            ack = response.json()
            if not isinstance(ack, dict) or ack.get("status") not in {
                "ready",
                "partial_failure",
            }:
                result["errors"].append(
                    "TrustChain returned an invalid avatar sync ACK"
                )
                continue
            failures = {
                str(item.get("event_id") or ""): item
                for item in ack.get("failed") or []
                if isinstance(item, dict) and item.get("event_id")
            }
            for event in selected:
                event_id = str(event.get("event_id") or "")
                failure = failures.get(event_id)
                if failure is None:
                    _write_synced(receipts, event, ack)
                    delivered_event_ids.add(event_id)
                    continue
                status = str(failure.get("status") or "")
                if status in _PERMANENT_REJECTION_STATUSES:
                    rejection = {
                        "event_id": event_id,
                        "status": status,
                        "disposition": "quarantined",
                    }
                    _write_synced(receipts, event, rejection)
                    quarantined_event_ids.add(event_id)
                    result["quarantined"] += 1
                    result["rejections"].append(rejection)
                else:
                    result["errors"].append(failure)
            result["accepted"] += int(ack.get("accepted") or 0)
            result["duplicates"] += int(ack.get("duplicates") or 0)
        normal_pending_ids = {
            str(event.get("event_id") or "") for event in events
            if not _read_synced(receipts, event)
        }
        identity_bootstrap = (
            not reconciliation_before.get("ok")
            and reconciliation_before.get("remote_status") == "identity_unbound"
        )
        should_verify_repair = bool(reconciliation_before.get("ok")) or (
            identity_bootstrap and bool(delivered_event_ids)
        )
        reconciliation_after = reconciliation_before
        if should_verify_repair:
            reconcilable_after = [
                event for event in events
                if not _read_quarantined(receipts, event)
            ]
            reconciliation_after = _reconcile_with_trustchain_avatar(
                client=client,
                base_url=base_url,
                bearer=bearer,
                avatar_id=resolved_avatar_id,
                events=reconcilable_after,
            )

        after_ok = bool(reconciliation_after.get("ok"))
        remaining_missing_ids = set(
            reconciliation_after.get("missing_event_ids") or []
            if after_ok else missing_event_ids
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
        present_after = set(
            reconciliation_after.get("present_event_ids") or []
        )
        verified_after = set(
            reconciliation_after.get("verified_present_event_ids") or []
        )
        repaired_ids = (
            missing_event_ids & present_after if after_ok else set()
        )
        reverified_ids = (
            unverified_event_ids & verified_after
            if after_ok and verification_classified else set()
        )
        reconciliation_errors = list(
            reconciliation_after.get("errors") or []
        )
        if reconciliation_errors:
            result["errors"].extend(reconciliation_errors)
        result["pending"] = len(
            normal_pending_ids
            | remaining_missing_ids
            | remaining_unverified_ids
        )
        result["quarantined_total"] = sum(
            _read_quarantined(receipts, event) for event in events
        )
        conflict_ids = list(
            reconciliation_after.get("conflict_event_ids") or []
        )
        storage_complete = bool(
            reconciliation_after.get("storage_complete")
        )
        verification_complete = bool(
            reconciliation_after.get("verification_complete")
        )
        reconciliation_complete = bool(
            reconciliation_after.get("complete")
        )
        if not reconciliation_before.get("ok") and not identity_bootstrap:
            reconciliation_status = "reconciliation_failed"
        elif not after_ok:
            reconciliation_status = "reconciliation_failed"
        elif conflict_ids:
            reconciliation_status = "identity_conflict"
        elif remaining_missing_ids:
            reconciliation_status = "repair_incomplete"
        elif remaining_unverified_ids:
            reconciliation_status = "verification_incomplete"
        elif missing_event_ids and unverified_event_ids:
            reconciliation_status = "repaired_and_reverified"
        elif missing_event_ids:
            reconciliation_status = "repaired"
        elif unverified_event_ids:
            reconciliation_status = "reverified"
        else:
            reconciliation_status = "in_sync"
        result["reconciliation"] = {
            "ok": after_ok,
            "status": reconciliation_status,
            "complete": reconciliation_complete,
            "storage_complete": storage_complete,
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
            "repaired_count": len(repaired_ids),
            "reverified_count": len(reverified_ids),
            "remaining_missing_count": len(remaining_missing_ids),
            "remaining_unverified_count": len(remaining_unverified_ids),
            "conflict_count": len(conflict_ids),
            "conflict_event_ids": conflict_ids[:20],
            "errors": reconciliation_errors[:20],
        }
        result["ok"] = not result["errors"] and reconciliation_complete
        return result
    except Exception as exc:
        result["errors"].append(str(exc))
        return result
    finally:
        if close_client:
            client.close()
