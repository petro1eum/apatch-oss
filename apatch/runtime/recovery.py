"""One-call deterministic recovery for one exact governed session."""

from __future__ import annotations

import os
from typing import Any, Dict


def recover_session(target_dir: str, governed_session_id: str) -> Dict[str, Any]:
    from apatch.gc import run_gc
    from apatch.lane import lane_state_path_for
    from apatch.lane_context import resolve_lane_for_session
    from apatch.runtime.atomic_io import read_json_file
    from apatch.runtime.finalization import (
        begin_finalization,
        finalize_session,
        load_finalization,
    )
    from apatch.path_leases import sweep_stale_leases

    root = os.path.abspath(target_dir)
    session_id = str(governed_session_id or "").strip()
    if not session_id:
        return {
            "ok": False,
            "error": "governed_session_id is required for recovery",
            "error_type": "SESSION_BINDING_REQUIRED",
            "recoverable": True,
        }

    journal = load_finalization(root, session_id)
    if journal:
        result = finalize_session(root, journal)
        return {**result, "recovery": "closed" if result.get("ok") else "retry"}

    lane_id, lane_error = resolve_lane_for_session(root, session_id, active_only=False)
    if lane_error or not lane_id:
        return {
            "ok": False,
            "error": "The requested governed session is not registered in this workspace.",
            "error_type": "SESSION_MISMATCH",
            "recoverable": False,
            "expected": session_id,
        }

    state = read_json_file(lane_state_path_for(root, lane_id, "session_state.json"), {})
    if str(state.get("session_id") or "") != session_id:
        return {
            "ok": False,
            "error": "The requested lane belongs to another governed session.",
            "error_type": "SESSION_MISMATCH",
            "recoverable": False,
            "expected": session_id,
            "actual": state.get("session_id"),
        }

    if state.get("ended_at") or state.get("attested") or state.get("phase") == "complete":
        journal = begin_finalization(
            root,
            session_id=session_id,
            lane_id=lane_id,
            session_token_hash=str(state.get("session_token_hash") or ""),
        )
        result = finalize_session(root, journal)
        return {**result, "recovery": "closed" if result.get("ok") else "retry"}

    swept = sweep_stale_leases(root)
    lease_action = "released_stale" if swept else "none"

    from apatch.runtime.runtime import MutationRuntime

    resumed = MutationRuntime(root, session_id=session_id).resume_session()
    if not resumed.get("ok"):
        return {**resumed, "recovery": "blocked"}
    from apatch.runtime.effects import EffectRecoveryError, recover_source_effects
    from apatch.runtime.errors import SessionBindingError
    from apatch.sandbox import SandboxError
    runtime = MutationRuntime(root, session_id=session_id,
                              session_token=resumed["session_token"], enforce_binding=True)
    try:
        effects = recover_source_effects(runtime)
    except SessionBindingError as exc:
        return {**resumed, **exc.to_dict(), "recovery": "blocked"}
    except SandboxError as exc:
        return {**resumed, "ok": False, "recovery": "blocked", "error": str(exc),
                "error_type": exc.error_type, "details": exc.details, "recoverable": True}
    except EffectRecoveryError as exc:
        return {**resumed, "ok": False, "recovery": "blocked", "error": str(exc),
                "error_type": exc.code, "recoverable": True,
                "recommended_action": "Preserve the checkpoint; reconcile exact signed effects or perform native rollback."}
    if not effects.get("ok"):
        return {**resumed, **effects, "recovery": "blocked"}
    if effects.get("recovery") == "source_effects_notarized":
        resumed.update(effects)
        resumed["resume_mode"] = "apply" if effects.get("continue") else "verify"
        resumed["next_action"] = ("apatch_apply_session()" if effects.get("continue")
                                  else "apatch_verify_run() then apatch_attest()")
    hygiene = run_gc(root, mode="safe")
    return {
        **resumed,
        "recovery": "resumed",
        "lease_action": lease_action,
        "released_lease_ids": swept,
        "hygiene": {
            "deleted_count": int(hygiene.get("deleted_count") or 0),
            "reclaimed_bytes": int(hygiene.get("reclaimed_bytes") or 0),
        },
    }
