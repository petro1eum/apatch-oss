"""Idempotency journal for retryable MCP orchestration requests."""

from __future__ import annotations

import hashlib
import json
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock, read_json_file


REQUEST_JOURNAL_REL = ".apatch/request_journal.json"
_MAX_RECORDS = 64
_REQUEST_CONTEXT: ContextVar[Optional[tuple[str, str]]] = ContextVar(
    "apatch_request_context", default=None
)


def request_journal_path(target_dir: str) -> str:
    return os.path.join(os.path.abspath(target_dir), REQUEST_JOURNAL_REL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _redact_capabilities(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>" if str(key) == "session_token" else _redact_capabilities(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_capabilities(item) for item in value]
    return _json_safe(value)


def _session_id(result: Mapping[str, Any]) -> Optional[str]:
    capability = result.get("session_capability")
    if isinstance(capability, Mapping) and capability.get("session_id"):
        return str(capability["session_id"])
    session = result.get("session")
    if isinstance(session, Mapping) and session.get("session_id"):
        return str(session["session_id"])
    if result.get("session_id"):
        return str(result["session_id"])
    return None


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _json_safe(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _digest(canonical)


def _load(path: str) -> Dict[str, Any]:
    raw = read_json_file(path, {"version": 1, "requests": {}})
    if not isinstance(raw.get("requests"), dict):
        raw["requests"] = {}
    raw["version"] = 1
    return raw


def _prune(data: Dict[str, Any]) -> None:
    requests = data.get("requests") or {}
    if len(requests) <= _MAX_RECORDS:
        return
    ordered = sorted(
        requests.items(), key=lambda item: str((item[1] or {}).get("updated_at") or "")
    )
    for key, _record in ordered[: len(requests) - _MAX_RECORDS]:
        requests.pop(key, None)


def current_request_hash(target_dir: str) -> Optional[str]:
    """Return the active request hash for session-state crash correlation."""
    context = _REQUEST_CONTEXT.get()
    if context and context[0] == os.path.abspath(target_dir):
        return context[1]
    return None


def bind_request_session(target_dir: str, session_id: str) -> None:
    """Persist session identity as soon as start_session commits its state."""
    root = os.path.abspath(target_dir)
    request_hash = current_request_hash(root)
    if not request_hash:
        return
    path = request_journal_path(root)
    with exclusive_file_lock(path):
        data = _load(path)
        record = (data.get("requests") or {}).get(request_hash)
        if not isinstance(record, dict):
            return
        record.update(
            {
                "session_id": str(session_id),
                "updated_at": _now(),
            }
        )
        data["requests"][request_hash] = record
        atomic_write_json(path, data)


def _pid_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _find_request_session(root: str, request_hash: str) -> Optional[Dict[str, str]]:
    apatch_dir = Path(root) / ".apatch"
    paths = [apatch_dir / "session_state.json"]
    lanes_dir = apatch_dir / "lanes"
    if lanes_dir.is_dir():
        paths.extend(lanes_dir.glob("*/session_state.json"))
    for state_path in paths:
        state = read_json_file(str(state_path), {})
        if state.get("request_id_hash") != request_hash or not state.get("session_id"):
            continue
        lane_id = "default"
        if state_path.parent.parent == lanes_dir:
            lane_id = state_path.parent.name
        return {
            "session_id": str(state["session_id"]),
            "lane_id": lane_id,
        }
    return None


def _recover_interrupted_request(
    root: str,
    *,
    request_hash: str,
    operation: str,
    session: Mapping[str, str],
) -> Dict[str, Any]:
    from apatch.runtime.recovery import recover_session

    session_id = str(session["session_id"])
    recovery = recover_session(root, session_id)
    if recovery.get("error_type") == "SESSION_MISMATCH":
        # start_session writes state before registry registration. A hard crash
        # in that narrow window is repairable because request hash + session id
        # uniquely prove the orphan lane's ownership.
        from apatch.lane_context import register_active_lane

        register_active_lane(root, str(session["lane_id"]), session_id=session_id)
        recovery = recover_session(root, session_id)
    out: Dict[str, Any] = {
        "ok": bool(recovery.get("ok")),
        "interrupted_request_recovered": bool(recovery.get("ok")),
        "original_outcome_known": False,
        "operation": operation,
        "session_id": session_id,
        "request_id_hash": request_hash,
        "request_recovery": recovery,
        "recommended_action": recovery.get("next_action") or "inspect the recovered session",
    }
    if not recovery.get("ok"):
        out.update(
            {
                "error": recovery.get("error") or "interrupted request recovery failed",
                "error_type": recovery.get("error_type") or "REQUEST_RECOVERY_FAILED",
                "recoverable": bool(recovery.get("recoverable", True)),
            }
        )
    token = recovery.get("session_token")
    if token:
        out["session_token"] = token
        out["session_capability"] = {
            "session_id": session_id,
            "session_token": token,
        }
    return out


def _complete_record(
    path: str,
    request_hash: str,
    result: Mapping[str, Any],
) -> None:
    safe_result = _redact_capabilities(result)
    assert isinstance(safe_result, dict)
    with exclusive_file_lock(path):
        data = _load(path)
        record = (data.get("requests") or {}).get(request_hash) or {}
        record.update(
            {
                "status": "completed",
                "updated_at": _now(),
                "session_id": _session_id(result) or record.get("session_id"),
                "result": safe_result,
            }
        )
        data["requests"][request_hash] = record
        _prune(data)
        atomic_write_json(path, data)


def _replay_result(target_dir: str, cached: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cached)
    session_id = _session_id(out)
    if not session_id or not out.get("ok"):
        return out
    from apatch.runtime.recovery import recover_session

    recovery = recover_session(target_dir, session_id)
    out["request_recovery"] = {
        key: recovery.get(key)
        for key in ("ok", "recovery", "lifecycle", "resume_mode", "error_type")
        if recovery.get(key) is not None
    }
    token = recovery.get("session_token")
    if token:
        out["session_token"] = token
        out["session_capability"] = {
            "session_id": session_id,
            "session_token": token,
        }
    return out


def run_idempotent_request(
    target_dir: str,
    *,
    operation: str,
    request_id: Optional[str],
    payload: Mapping[str, Any],
    execute: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    """Run once per request id and make client-timeout retries deterministic."""
    raw_request_id = str(request_id or "").strip()
    if not raw_request_id:
        return execute()
    if len(raw_request_id) < 16 or len(raw_request_id) > 256:
        return {
            "ok": False,
            "error": "request_id must contain 16..256 characters",
            "error_type": "REQUEST_ID_INVALID",
            "recoverable": True,
        }

    root = os.path.abspath(target_dir)
    path = request_journal_path(root)
    request_hash = _digest(raw_request_id)
    payload_hash = _fingerprint(payload)
    recovered_session: Optional[Dict[str, str]] = None
    execute_request = False
    with exclusive_file_lock(path):
        data = _load(path)
        record = (data.get("requests") or {}).get(request_hash)
        if record:
            if record.get("operation") != operation or record.get("payload_sha256") != payload_hash:
                return {
                    "ok": False,
                    "error": "request_id was already used for a different operation or payload",
                    "error_type": "REQUEST_ID_CONFLICT",
                    "recoverable": False,
                    "request_id_hash": request_hash,
                }
            if record.get("status") == "completed":
                cached = record.get("result")
                if isinstance(cached, dict):
                    replayed = _replay_result(root, cached)
                    replayed["idempotent_replay"] = True
                    replayed["request_id_hash"] = request_hash
                    return replayed
            recovered_session = _find_request_session(root, request_hash)
            if recovered_session is None and record.get("session_id"):
                recovered_session = {
                    "session_id": str(record["session_id"]),
                    "lane_id": str(record.get("lane_id") or "default"),
                }
            if recovered_session is None and not _pid_alive(record.get("owner_pid")):
                record.update(
                    {
                        "status": "started",
                        "owner_pid": os.getpid(),
                        "updated_at": _now(),
                    }
                )
                data["requests"][request_hash] = record
                atomic_write_json(path, data)
                execute_request = True
            elif recovered_session is None:
                error_type = (
                    "REQUEST_IN_PROGRESS"
                    if record.get("status") == "started"
                    else "REQUEST_OUTCOME_UNKNOWN"
                )
                return {
                    "ok": False,
                    "error": "the original request is still running or its outcome is unknown",
                    "error_type": error_type,
                    "recoverable": True,
                    "recommended_action": "retry_same_request_id",
                    "request_id_hash": request_hash,
                    "session_id": record.get("session_id"),
                }

        else:
            now = _now()
            data["requests"][request_hash] = {
                "operation": operation,
                "payload_sha256": payload_hash,
                "status": "started",
                "owner_pid": os.getpid(),
                "started_at": now,
                "updated_at": now,
            }
            _prune(data)
            atomic_write_json(path, data)
            execute_request = True

    if recovered_session is not None:
        recovered = _recover_interrupted_request(
            root,
            request_hash=request_hash,
            operation=operation,
            session=recovered_session,
        )
        _complete_record(path, request_hash, recovered)
        return recovered

    assert execute_request
    context_token = _REQUEST_CONTEXT.set((root, request_hash))
    try:
        result = execute()
    except BaseException:
        with exclusive_file_lock(path):
            data = _load(path)
            record = (data.get("requests") or {}).get(request_hash) or {}
            record.update({"status": "outcome_unknown", "updated_at": _now()})
            data["requests"][request_hash] = record
            atomic_write_json(path, data)
        raise
    finally:
        _REQUEST_CONTEXT.reset(context_token)

    _complete_record(path, request_hash, result)
    out = dict(result)
    out["idempotent_replay"] = False
    out["request_id_hash"] = request_hash
    return out
