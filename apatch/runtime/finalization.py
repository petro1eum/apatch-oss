"""Retryable, exact-session finalization for governed operations."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

FINALIZATIONS_REL = os.path.join(".apatch", "state", "finalizations.json")
MAX_FINALIZATION_RECORDS = 256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def finalizations_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), FINALIZATIONS_REL)


def _load_unlocked(path: str) -> Dict[str, Any]:
    from apatch.runtime.atomic_io import read_json_file

    data = read_json_file(path, {})
    if not isinstance(data, dict):
        data = {}
    records = data.get("records")
    if not isinstance(records, dict):
        records = {}
    return {"version": 1, "records": records}


def load_finalization(root: str, session_id: str) -> Optional[Dict[str, Any]]:
    data = _load_unlocked(finalizations_path(root))
    row = data["records"].get(str(session_id))
    return dict(row) if isinstance(row, dict) else None


def _store_record(root: str, session_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock

    path = finalizations_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with exclusive_file_lock(path):
        data = _load_unlocked(path)
        records = data["records"]
        records[str(session_id)] = dict(record)
        if len(records) > MAX_FINALIZATION_RECORDS:
            ordered = sorted(
                records.items(),
                key=lambda item: str(item[1].get("updated_at") or ""),
            )
            for old_id, _ in ordered[: len(records) - MAX_FINALIZATION_RECORDS]:
                records.pop(old_id, None)
        atomic_write_json(path, data)
    try:
        from apatch.artifact_governance import register_on_write

        register_on_write(
            root,
            FINALIZATIONS_REL,
            class_name="STATE",
            created_by_tool="apatch_session_end",
            reason="bounded idempotent finalization journal",
            replay_critical=True,
            gc_allowed=False,
        )
    except Exception:
        pass
    return dict(record)


def begin_finalization(
    root: str,
    *,
    session_id: str,
    lane_id: str,
    session_token_hash: str,
) -> Dict[str, Any]:
    existing = load_finalization(root, session_id)
    if existing:
        return existing
    now = _now()
    return _store_record(
        root,
        session_id,
        {
            "session_id": session_id,
            "lane_id": lane_id,
            "session_token_hash": session_token_hash,
            "status": "finalizing",
            "steps": {},
            "created_at": now,
            "updated_at": now,
        },
    )


def validate_finalization_token(
    record: Dict[str, Any],
    *,
    session_token: Optional[str],
    require_binding: bool,
) -> Optional[Dict[str, Any]]:
    expected_hash = str(record.get("session_token_hash") or "")
    if require_binding and expected_hash and not session_token:
        return {
            "ok": False,
            "error": "This governed session finalization requires its session token.",
            "error_type": "SESSION_BINDING_REQUIRED",
            "expected": record.get("session_id"),
        }
    if session_token and (
        not expected_hash
        or not hmac.compare_digest(
            hashlib.sha256(session_token.encode("utf-8")).hexdigest(), expected_hash
        )
    ):
        return {
            "ok": False,
            "error": "The governed session token is invalid.",
            "error_type": "SESSION_TOKEN_MISMATCH",
            "expected": record.get("session_id"),
        }
    return None


def _run_step(
    root: str,
    record: Dict[str, Any],
    name: str,
    operation: Callable[[], Any],
) -> Dict[str, Any]:
    steps = dict(record.get("steps") or {})
    if name in steps:
        return record
    result = operation()
    if isinstance(result, dict):
        summary = {
            key: result.get(key)
            for key in ("ok", "deleted_count", "reclaimed_bytes", "released")
            if key in result
        }
    elif isinstance(result, (bool, int, float, str)) or result is None:
        summary = result
    elif isinstance(result, (list, tuple, set)):
        summary = {"count": len(result)}
    else:
        summary = {"completed": True}
    steps[name] = {"completed_at": _now(), "result": summary}
    record = {**record, "steps": steps, "updated_at": _now()}
    return _store_record(root, str(record["session_id"]), record)


def finalize_session(root: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Converge all finalization steps; safe to call repeatedly after interruption."""
    from apatch.artifact_governance import (
        delete_gc_allowed_session_ephemerals,
        release_session_registry_cleanup,
    )
    from apatch.gc import run_gc
    from apatch.lane import lane_state_path_for
    from apatch.lane_context import unregister_lane
    from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock, read_json_file
    from apatch.sandbox import release_lease

    root = os.path.abspath(root)
    session_id = str(record["session_id"])
    lane_id = str(record["lane_id"])

    # A journal entry is evidence of an attempted step, not a substitute for
    # its durable post-condition. Recovery must heal a partially restored or
    # externally interrupted state even when an earlier finalization completed.
    state_path = lane_state_path_for(root, lane_id, "session_state.json")
    persisted_state = read_json_file(state_path, {})
    if (
        str(persisted_state.get("session_id") or "") == session_id
        and not persisted_state.get("ended_at")
        and "state_ended" in (record.get("steps") or {})
    ):
        repaired_steps = dict(record.get("steps") or {})
        repaired_steps.pop("state_ended", None)
        record = _store_record(
            root,
            session_id,
            {**record, "steps": repaired_steps, "status": "finalizing"},
        )

    def end_state() -> bool:
        with exclusive_file_lock(state_path):
            state = read_json_file(state_path, {})
            if str(state.get("session_id") or "") != session_id:
                raise RuntimeError("session state was replaced before finalization")
            if not state.get("ended_at"):
                state["ended_at"] = _now()
                state["revision"] = int(state.get("revision") or 0) + 1
                state["updated_at"] = _now()
                atomic_write_json(state_path, state)
        return True

    operations = (
        ("state_ended", end_state),
        (
            "registry_released",
            lambda: release_session_registry_cleanup(root, session_id),
        ),
        (
            "ephemerals_deleted",
            lambda: delete_gc_allowed_session_ephemerals(root, session_id),
        ),
        (
            "writer_lease_released",
            lambda: release_lease(root, governed_session_id=session_id),
        ),
        ("hygiene_rotated", lambda: run_gc(root, mode="rotate")),
        (
            "lane_unregistered",
            lambda: unregister_lane(
                root, lane_id, expected_session_id=session_id
            ),
        ),
    )

    try:
        current = dict(record)
        for name, operation in operations:
            current = _run_step(root, current, name, operation)
    except Exception as exc:
        failed = {
            **current,
            "status": "finalizing",
            "updated_at": _now(),
            "last_error": type(exc).__name__,
        }
        _store_record(root, session_id, failed)
        return {
            "ok": False,
            "session_id": session_id,
            "status": "finalizing",
            "error": "Session cleanup was interrupted and can be retried safely.",
            "error_type": "SESSION_FINALIZATION_INCOMPLETE",
            "recoverable": True,
            "completed_steps": sorted((failed.get("steps") or {}).keys()),
            "recommended_action": "apatch_recover(governed_session_id=...)",
        }

    current = {
        **current,
        "status": "complete",
        "completed_at": current.get("completed_at") or _now(),
        "updated_at": _now(),
        "last_error": None,
    }
    current = _store_record(root, session_id, current)
    hygiene = (current.get("steps") or {}).get("hygiene_rotated", {}).get("result") or {}
    deleted = (current.get("steps") or {}).get("ephemerals_deleted", {}).get("result") or {}
    return {
        "ok": True,
        "session_id": session_id,
        "status": "complete",
        "idempotent": True,
        "completed_steps": sorted((current.get("steps") or {}).keys()),
        "cleanup": {
            "ephemeral_deleted_count": int(deleted.get("count") or 0),
            "history_deleted_count": int(hygiene.get("deleted_count") or 0),
            "reclaimed_bytes": int(hygiene.get("reclaimed_bytes") or 0),
        },
    }
