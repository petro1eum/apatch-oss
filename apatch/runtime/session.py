"""Session aggregate views and lifecycle commands."""

from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from apatch.doctor import build_policy_snapshot
from apatch.runtime.domain import (
    CORE_INVARIANT,
    LIFECYCLE_FAILED,
    LIFECYCLE_ROLLED_BACK,
    SCHEMA_VERSION,
    derive_lifecycle,
)
from apatch.runtime.models import Session
from apatch.runtime.events import emit_domain_event
from apatch.session_state import (
    PHASE_IDLE,
    load_session_state,
    save_session_state,
    session_state_path,
)


def _new_session_id() -> str:
    return "apatch_sess_{}_{}".format(time.time_ns(), secrets.token_hex(6))


def _has_active_session(target_dir: str) -> bool:
    raw = load_session_state(os.path.abspath(target_dir))
    return bool(
        raw.get("session_id")
        and raw.get("intent")
        and not raw.get("ended_at")
    )


def _raise_no_active_session(target_dir: str, *, operation: str) -> None:
    from apatch.runtime.errors import RuntimeTransitionError
    from apatch.runtime.hints import hint_session_start
    from apatch.runtime.state_machine import current_lifecycle

    root = os.path.abspath(target_dir)
    lifecycle = current_lifecycle(root)
    raise RuntimeTransitionError(
        "Governed session required. Start with apatch_session_start(intent=...).",
        lifecycle=lifecycle,
        operation=operation,
        hint=hint_session_start(),
    )


def ensure_governed_session(
    target_dir: str,
    *,
    operation: str,
    intent_hint: Optional[str] = None,
) -> None:
    """Apply governed_mode gate before mutations (off / auto_session / strict)."""
    from apatch.enforcement import (
        GOVERNED_MODE_AUTO_SESSION,
        GOVERNED_MODE_OFF,
        GOVERNED_MODE_STRICT,
        resolve_governed_mode,
    )

    root = os.path.abspath(target_dir)
    mode = resolve_governed_mode(root)
    if mode == GOVERNED_MODE_OFF:
        return
    if _has_active_session(root):
        return
    if mode == GOVERNED_MODE_STRICT:
        _raise_no_active_session(root, operation=operation)
    if mode == GOVERNED_MODE_AUTO_SESSION:
        intent = (intent_hint or f"auto: {operation} via MCP").strip()
        result = start_session(root, intent, auto_started=True)
        if not result.get("ok"):
            _raise_no_active_session(root, operation=operation)


def require_active_session(target_dir: str, *, operation: str = "phase_run") -> None:
    """Raise RuntimeTransitionError when no open session (strict branch only)."""
    if not _has_active_session(target_dir):
        _raise_no_active_session(target_dir, operation=operation)


def set_session_intent(
    target_dir: str,
    intent: str,
    *,
    force_new: bool = False,
    artifacts: Optional[Sequence[Union[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Set intent on active session, or start a new one."""
    from apatch.artifact import coerce_artifacts

    root = os.path.abspath(target_dir)
    if not intent or not intent.strip():
        return {"ok": False, "error": "intent is required"}
    from apatch.path_leases import writer_protocol_preflight

    protocol_error = writer_protocol_preflight(root)
    if protocol_error:
        return protocol_error

    prev = load_session_state(root)
    if (
        not force_new
        and prev.get("session_id")
        and not prev.get("ended_at")
    ):
        state = {**prev, "intent": intent.strip(), "failure": None}
        if artifacts is not None:
            state["artifacts"] = coerce_artifacts(artifacts)
        save_session_state(root, state)
        view = build_session_view(root)
        view["ok"] = True
        view["intent_updated"] = True
        return view

    if prev.get("session_id") and not prev.get("ended_at") and force_new:
        end_session(root)

    return start_session(target_dir, intent, artifacts=artifacts)


def start_session(
    target_dir: str,
    intent: str,
    *,
    auto_started: bool = False,
    artifacts: Optional[Sequence[Union[str, Dict[str, Any]]]] = None,
    artifact_files: Optional[Dict[str, List[str]]] = None,
    sdd_contract: Optional[Mapping[str, Any]] = None,
    task_envelope: Optional[Mapping[str, Any]] = None,
    actor: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Open a governed session with explicit Intent (G1/G2)."""
    from apatch.artifact import coerce_artifacts

    root = os.path.abspath(target_dir)
    if not intent or not intent.strip():
        return {"ok": False, "error": "intent is required"}
    from apatch.path_leases import writer_protocol_preflight

    protocol_error = writer_protocol_preflight(root)
    if protocol_error:
        return protocol_error

    try:
        from apatch.sdd_integrity import SddContractError, load_profile_contract

        profile_contract = load_profile_contract(root)
    except SddContractError as exc:
        return {
            "ok": False,
            "error_type": "SDD_CONTRACT_INVALID",
            "error": str(exc),
            "recoverable": True,
            "recommended_action": "repair_profile_manifest",
        }

    profile_inputs = (sdd_contract, task_envelope, actor)
    if profile_contract is not None:
        if not all(item is not None for item in profile_inputs):
            return {
                "ok": False,
                "error_type": "SDD_CONTRACT_REQUIRED",
                "error": "This workspace requires the frozen SDD contract, task envelope and implementation actor",
                "recoverable": True,
                "recommended_action": "start_sdd_session",
            }
        supplied_hash = (
            sdd_contract.get("document_hash")
            if isinstance(sdd_contract, Mapping)
            else None
        )
        if supplied_hash != profile_contract.get("document_hash"):
            return {
                "ok": False,
                "error_type": "SDD_CONTRACT_MISMATCH",
                "error": "The supplied SDD contract does not match the workspace's frozen contract",
                "recoverable": True,
                "recommended_action": "reload_frozen_contract",
            }

    prev = load_session_state(root)
    if prev.get("session_id") and not prev.get("ended_at"):
        failure = prev.get("failure") if isinstance(prev.get("failure"), dict) else None
        lifecycle = derive_lifecycle(
            prev.get("phase", "idle"),
            failure=failure,
        )
        if lifecycle in (LIFECYCLE_FAILED, LIFECYCLE_ROLLED_BACK):
            end_session(
                root,
                expected_session_id=str(prev.get("session_id") or ""),
                require_binding=False,
            )
            prev = load_session_state(root)
        else:
            return {
                "ok": False,
                "error_type": "SESSION_CONFLICT",
                "error": "active session exists",
                "session_id": prev.get("session_id"),
                "recoverable": True,
                "recommended_action": "use_existing_or_end_session",
                "hint": "apatch_session_end() — then apatch_session_start (or set_session_intent)",
            }

    from apatch.runtime.session_binding import hash_session_token

    session_id = _new_session_id()
    session_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    artifact_list = coerce_artifacts(artifacts) if artifacts is not None else []
    sdd_inputs = (sdd_contract, task_envelope, actor)
    sdd_binding: Optional[Dict[str, Any]] = None
    if any(item is not None for item in sdd_inputs):
        if not all(item is not None for item in sdd_inputs):
            return {
                "ok": False,
                "error_type": "SDD_CONTRACT_INCOMPLETE",
                "error": "sdd_contract, task_envelope and actor must be supplied together",
                "recoverable": True,
                "recommended_action": "freeze_verification_contract",
            }
        try:
            from apatch.sdd_integrity import build_session_binding

            sdd_binding = build_session_binding(
                sdd_contract or {},
                task_envelope or {},
                actor or {},
            )
        except Exception as exc:
            from apatch.sdd_integrity import SddContractError

            if not isinstance(exc, SddContractError):
                raise
            return {
                "ok": False,
                "error_type": "SDD_CONTRACT_INVALID",
                "error": str(exc),
                "recoverable": True,
                "recommended_action": "freeze_verification_contract",
            }
    from apatch.runtime.request_journal import current_request_hash

    request_id_hash = current_request_hash(root)
    state = {
        **prev,
        "session_id": session_id,
        "session_capability_version": 1,
        "session_token_hash": hash_session_token(session_token),
        "intent": intent.strip(),
        "artifacts": artifact_list,
        "artifact_files": dict(artifact_files or {}),
        "phase": PHASE_IDLE,
        "checkpoint": None,
        "started_at": now,
        "ended_at": None,
        "failure": None,
        "attested": False,
        "request_id_hash": request_id_hash,
    }
    # SDD authority and proof belong to one governed session, not to its lane.
    # Mandatory workspace-profile admission has already run above; clearing a
    # retired binding here must never substitute for that check.
    state.pop("sdd", None)
    if sdd_binding is not None:
        state["sdd"] = sdd_binding
    try:
        save_session_state(
            root,
            state,
            expected_session_id=prev.get("session_id"),
            expected_revision=int(prev.get("revision") or 0),
        )
    except Exception as exc:
        from apatch.runtime.errors import SessionBindingError

        if isinstance(exc, SessionBindingError):
            out = exc.to_dict()
            out["error_type"] = "SESSION_CONFLICT"
            out["error"] = "Another process opened or changed this session lane."
            return out
        raise
    from apatch.lane import resolve_lane
    from apatch.lane_context import bind_governed_session_id, register_active_lane

    register_active_lane(root, resolve_lane(root).lane_id, session_id=session_id)
    from apatch.runtime.request_journal import bind_request_session

    bind_request_session(root, session_id)
    # A previous session in this request may have bound its now-ended id into
    # the lane context. Rebind before build_session_view resolves the lane,
    # otherwise the fresh session is created but returned as an empty view.
    bind_governed_session_id(session_id, root=root)
    payload: Dict[str, Any] = {"intent": intent.strip(), "lifecycle": "draft"}
    if artifact_list:
        payload["artifacts"] = artifact_list
    if artifact_files:
        payload["artifact_files"] = dict(artifact_files)
    if sdd_binding is not None:
        payload["sdd"] = {
            "schema": sdd_binding["schema"],
            "contract_hash": sdd_binding["contract_hash"],
            "envelope_hash": sdd_binding["envelope_hash"],
            "actor": sdd_binding["actor"],
            "requirement": sdd_binding["requirement"],
            "containment": sdd_binding["containment"],
        }
    if auto_started:
        payload["auto_started"] = True
    emit_domain_event(
        root,
        "SessionStarted",
        payload,
        session_id=session_id,
    )
    view = build_session_view(root)
    view["ok"] = True
    view["session_token"] = session_token
    view["session_capability"] = {
        "session_id": session_id,
        "session_token": session_token,
    }
    return view


def continue_apply_session(
    target_dir: str,
    logs_path: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Resume chunked apply from saved checkpoint (RFP-004 session continue)."""
    from apatch.runtime.runtime import MutationRuntime

    rt = MutationRuntime(target_dir)
    return rt.apply_session(logs_path, reset=False, **kwargs)


def end_session(
    target_dir: str,
    *,
    expected_session_id: Optional[str] = None,
    session_token: Optional[str] = None,
    require_binding: bool = False,
) -> Dict[str, Any]:
    from apatch.runtime.finalization import (
        begin_finalization,
        finalize_session,
        load_finalization,
        validate_finalization_token,
    )
    from apatch.runtime.errors import SessionBindingError
    from apatch.runtime.session_binding import capture_session_binding

    root = os.path.abspath(target_dir)
    if expected_session_id:
        existing = load_finalization(root, expected_session_id)
        if existing:
            from apatch.lane import lane_state_path_for
            from apatch.runtime.atomic_io import read_json_file

            lane_state = read_json_file(
                lane_state_path_for(root, str(existing.get("lane_id") or "default"), "session_state.json"),
                {},
            )
            actual_id = str(lane_state.get("session_id") or "")
            if actual_id and actual_id != expected_session_id and not lane_state.get("ended_at"):
                return SessionBindingError(
                    "The finalized lane now belongs to a replacement session.",
                    error_type="SESSION_MISMATCH",
                    expected=expected_session_id,
                    actual=actual_id,
                ).to_dict()
            token_error = validate_finalization_token(
                existing,
                session_token=session_token,
                require_binding=require_binding,
            )
            if token_error:
                return token_error
            result = finalize_session(root, existing)
            if result.get("ok"):
                from apatch.lane_context import bind_governed_session_id

                bind_governed_session_id(expected_session_id, root=root)
                return {**build_session_view(root), **result}
            return result
    try:
        binding = capture_session_binding(
            root,
            expected_session_id=expected_session_id,
            session_token=session_token,
            require_capability=require_binding,
            operation="session_end",
        )
    except SessionBindingError as exc:
        return exc.to_dict()
    if binding is None:
        return {"ok": False, "error": "no active session"}
    record = begin_finalization(
        root,
        session_id=binding.session_id,
        lane_id=binding.lane_id,
        session_token_hash=binding.session_token_hash,
    )
    result = finalize_session(root, record)
    if result.get("ok"):
        return {**build_session_view(root), **result}
    return result


def load_typed_session(target_dir: str) -> Session:
    """Typed Session aggregate from persisted state."""
    return Session.from_view(build_session_view(target_dir))


def build_session_view(target_dir: str) -> Dict[str, Any]:
    """Domain projection of Session aggregate for CLI/MCP/Console."""
    root = os.path.abspath(target_dir)
    raw = load_session_state(root)
    policy_snap = build_policy_snapshot(root)
    tc = policy_snap.get("trustchain") or {}
    failure = raw.get("failure") if isinstance(raw.get("failure"), dict) else None
    ended = bool(raw.get("ended_at"))
    lifecycle = derive_lifecycle(
        raw.get("phase", "idle"),
        attested=bool(raw.get("attested")),
        failure=failure if not ended else None,
        ended=ended,
    )

    invariant_ok = bool(raw.get("session_id")) and bool(raw.get("intent"))
    if ended:
        invariant_ok = invariant_ok  # ended sessions still valid historically

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "workspace": root,
        "core_invariant": CORE_INVARIANT,
        "session": {
            "session_id": raw.get("session_id"),
            "intent": raw.get("intent"),
            "artifacts": raw.get("artifacts") or [],
            "lifecycle": lifecycle,
            "phase": raw.get("phase"),
            "checkpoint": raw.get("checkpoint"),
            "risk_level": raw.get("risk_level"),
            "last_tool": raw.get("last_tool"),
            "next_action": raw.get("next_action"),
            "started_at": raw.get("started_at"),
            "ended_at": raw.get("ended_at"),
            "budget_remaining": raw.get("budget_remaining"),
            "failure": failure,
        },
        "policy": {
            "trustchain_mode": tc.get("mode"),
            "enforcement": policy_snap.get("enforcement", {}).get("active"),
            "sandbox_mode": (policy_snap.get("sandbox") or {}).get("mode"),
        },
        "invariant": {
            "has_intent": bool(raw.get("intent")),
            "has_session_id": bool(raw.get("session_id")),
            "satisfied": invariant_ok and not ended,
        },
        "session_state_path": session_state_path(root),
    }
