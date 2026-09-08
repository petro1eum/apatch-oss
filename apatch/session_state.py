"""R53 — agent session state machine persisted in .apatch/session_state.json."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from apatch.failure_taxonomy import FailureInfo, classify_failure

SESSION_STATE_REL = ".apatch/session_state.json"

PHASE_IDLE = "idle"
PHASE_PLAN = "plan"
PHASE_APPLY = "apply"
PHASE_VERIFY = "verify"
PHASE_ARCH = "arch"
PHASE_DB = "db"
PHASE_ROLLBACK = "rollback"
PHASE_COMPLETE = "complete"
PHASE_BLOCKED = "blocked"

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

_READ_ONLY_LIFECYCLE_TOOLS = frozenset({
    "apatch_asset_summary",
    "apatch_attestation_show",
    "apatch_doctor",
    "apatch_extension_list",
    "apatch_extension_inspect",
    "apatch_extension_validate",
    "apatch_extension_run",
    "apatch_events_tail",
    "apatch_index_query",
    "apatch_knowledge_graph",
    "apatch_project_status",
    "apatch_rfp_lint",
    "apatch_rfp_spec_coverage",
    "apatch_sandbox_audit",
    "apatch_sandbox_ci_gate",
    "apatch_sandbox_status",
    "apatch_session_state",
    "apatch_slug_cockpit",
    "apatch_slug_feedback_lint",
    "apatch_slug_intake",
    "apatch_spec_adherence",
    "apatch_spec_aggregate",
    "apatch_spec_coverage",
    "apatch_spec_interference",
    "apatch_spec_lint",
    "apatch_spec_needles_scaffold",
    "apatch_spec_next",
    "apatch_spec_plan_diff",
    "apatch_spec_plan_lint",
    "apatch_spec_plan_show",
    "apatch_spec_run_manifest_lint",
    "apatch_spec_schedule",
    "apatch_spec_status",
    "apatch_timesheet",
    "apatch_trustchain_coverage",
    "apatch_trustchain_history",
    "apatch_view",
    "apatch_work_asset_export",
    "apatch_work_asset_export_schema",
    "apatch_work_asset_recall",
    "apatch_work_asset_search",
    "apatch_work_asset_show",
    "apatch_work_asset_suggest",
    "apatch_work_assets",
    "apatch_workspace_inspect",
    "apatch_workspace_list",
})

# These tools can update their own durable transport/outbox state, but they do
# not participate in the governed code-mutation lifecycle.
_SESSION_NEUTRAL_OPERATIONAL_TOOLS = frozenset({
    "apatch_avatar_evidence_sync",
})

_PRE_SESSION_ORCHESTRATORS = frozenset({"apatch_execute_next", "apatch_spec_run"})
_PRE_SESSION_STEPS = frozenset({"lint", "dependencies", "spec_next", "finalize"})


_SESSION_BINDING_REJECTION_TYPES = frozenset({
    "PATCH_LOG_DIGEST_MISMATCH",
    "PATCH_LOG_DIGEST_MISSING",
    "PATCH_LOG_MISSING",
    "PATCH_LOG_OWNER_MISMATCH",
    "PATCH_LOG_UNOWNED",
    "PATCH_LOG_UNREGISTERED",
    "ROLLBACK_CHECKPOINT_MISMATCH",
    "ROLLBACK_CHECKPOINT_MISSING",
    "SESSION_AMBIGUOUS",
    "SESSION_BINDING_REQUIRED",
    "SESSION_CONFLICT",
    "SESSION_MISMATCH",
    "SESSION_REVISION_MISMATCH",
    "SESSION_TOKEN_MISMATCH",
})


_MUTATION_SUCCESS_TOOLS = frozenset({
    "apatch_apply",
    "apatch_strip",
    "apatch_phase_run",
    "apatch_governed_phase_run",
    "apatch_pipeline_run",
})

TOOL_DEFAULT_PHASE = {
    "apatch_doctor": PHASE_IDLE,
    "apatch_plan": PHASE_PLAN,
    "apatch_plan_batch": PHASE_PLAN,
    "apatch_generate": PHASE_PLAN,
    "apatch_generate_batch": PHASE_PLAN,
    "apatch_apply": PHASE_APPLY,
    "apatch_apply_session": PHASE_APPLY,
    "apatch_verify_notarization": PHASE_VERIFY,
    "apatch_verify_semantic": PHASE_VERIFY,
    "apatch_arch_check": PHASE_ARCH,
    "apatch_db_check": PHASE_DB,
    "apatch_db_safety": PHASE_DB,
    "apatch_db_revision": PHASE_DB,
    "apatch_db_run": PHASE_DB,
    "apatch_impact": PHASE_PLAN,
    "apatch_rollback": PHASE_ROLLBACK,
    "apatch_pipeline_run": PHASE_APPLY,
    "apatch_strip": PHASE_APPLY,
    "apatch_phase_run": PHASE_APPLY,
    "apatch_governed_phase_run": PHASE_APPLY,
    "apatch_plan_graph": PHASE_PLAN,
    "apatch_execute_graph": PHASE_APPLY,
    "apatch_orchestrate": PHASE_APPLY,
    "apatch_replay": PHASE_IDLE,
    "apatch_attest": PHASE_COMPLETE,
    "apatch_verify_status": PHASE_VERIFY,
    "apatch_sandbox_status": PHASE_IDLE,
    "apatch_sandbox_audit": PHASE_IDLE,
    "apatch_sandbox_ci_gate": PHASE_IDLE,
    "apatch_session_state": PHASE_IDLE,
    "apatch_session_start": PHASE_IDLE,
    "apatch_session_end": PHASE_IDLE,
    "apatch_verify_run": PHASE_VERIFY,
    "apatch_attestation_export": PHASE_COMPLETE,
    "apatch_events_tail": PHASE_IDLE,
    "apatch_simulate": PHASE_PLAN,
    "apatch_execute_next": PHASE_PLAN,
    "apatch_spec_run": PHASE_APPLY,
    "apatch_spec_run_manifest_lint": PHASE_PLAN,
}


def session_state_path(root: str) -> str:
    from apatch.lane import lane_state_path

    return lane_state_path(root, "session_state.json")


_EXPECTED_SESSION_UNSET = object()


def _read_state_path(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return _default_state()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    return {**_default_state(), **data}


def load_session_state(root: str) -> Dict[str, Any]:
    return _read_state_path(session_state_path(root))


def _default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "revision": 0,
        "phase": PHASE_IDLE,
        "checkpoint": None,
        "last_tool": None,
        "budget_remaining": None,
        "trustchain": False,
        "risk_level": RISK_LOW,
        "next_action": "apatch_doctor(target_dir='.')",
        "failure": None,
        "updated_at": None,
    }


_PERSIST_KEYS = (
    "phase",
    "checkpoint",
    "last_tool",
    "budget_remaining",
    "trustchain",
    "risk_level",
    "next_action",
    "failure",
    "attested",
)


def _persist_slice(state: Dict[str, Any]) -> Dict[str, Any]:
    return {k: state.get(k) for k in _PERSIST_KEYS}


# L1-6 write-behind: persist only when these fields change (not last_tool/next_action alone).
_WRITE_TRIGGER_KEYS = ("phase", "checkpoint", "failure", "attested")


def _write_trigger_slice(state: Dict[str, Any]) -> Dict[str, Any]:
    return {k: state.get(k) for k in _WRITE_TRIGGER_KEYS}


def session_state_changed(prev: Dict[str, Any], new: Dict[str, Any]) -> bool:
    return _write_trigger_slice(prev) != _write_trigger_slice(new)


def save_session_state(
    root: str,
    state: Dict[str, Any],
    *,
    force: bool = True,
    expected_session_id: Any = _EXPECTED_SESSION_UNSET,
    expected_revision: Optional[int] = None,
) -> str:
    from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock
    from apatch.runtime.errors import SessionBindingError

    path = session_state_path(root)
    with exclusive_file_lock(path):
        prev = _read_state_path(path)
        actual_session_id = prev.get("session_id")
        if (
            expected_session_id is not _EXPECTED_SESSION_UNSET
            and actual_session_id != expected_session_id
        ):
            raise SessionBindingError(
                "Session state compare-and-swap rejected a replacement session.",
                error_type="SESSION_MISMATCH",
                expected=expected_session_id,
                actual=actual_session_id,
            )
        actual_revision = int(prev.get("revision") or 0)
        if expected_revision is not None and actual_revision != int(expected_revision):
            raise SessionBindingError(
                "Session state revision changed during the operation.",
                error_type="SESSION_REVISION_MISMATCH",
                expected=str(expected_revision),
                actual=str(actual_revision),
            )
        if not force and not session_state_changed(prev, state):
            return path
        payload = dict(state)
        payload["revision"] = actual_revision + 1
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(path, payload)
        state.clear()
        state.update(payload)

    from apatch.artifact_governance import register_on_write

    register_on_write(
        root,
        SESSION_STATE_REL,
        class_name="STATE",
        created_by_tool="apatch_session_state",
        reason="session lifecycle state",
        governed_session_id=state.get("session_id"),
    )
    return path


def _derive_risk(result: Dict[str, Any], failure: Optional[FailureInfo]) -> str:
    if failure and failure.error_type in ("TRUSTCHAIN_REJECTED", "NOTARIZATION_FAILED"):
        return RISK_HIGH
    progress = result.get("progress") or {}
    total = progress.get("chunks_total") or result.get("total") or 0
    if total > 50:
        return RISK_HIGH
    if total > 15 or (failure and failure.recoverable):
        return RISK_MEDIUM
    violations = result.get("violations") or []
    if len(violations) > 3:
        return RISK_HIGH
    if violations:
        return RISK_MEDIUM
    return RISK_LOW


def _derive_phase(tool_name: str, result: Dict[str, Any], failure: Optional[FailureInfo]) -> str:
    if failure:
        return PHASE_BLOCKED
    if tool_name == "apatch_execute_next":
        execution_phase = result.get("execution_phase")
        if result.get("continue") or execution_phase == "mutate":
            return PHASE_APPLY
        if execution_phase == "verify":
            return PHASE_VERIFY
        if execution_phase == "complete":
            return PHASE_COMPLETE
    if tool_name in _MUTATION_SUCCESS_TOOLS and result.get("ok"):
        return PHASE_VERIFY
    if tool_name == "apatch_apply_session":
        if result.get("continue"):
            return PHASE_APPLY
        if result.get("ok"):
            return PHASE_VERIFY
        return PHASE_BLOCKED
    if tool_name == "apatch_rollback":
        return PHASE_IDLE if result.get("ok") else PHASE_BLOCKED
    if tool_name == "apatch_verify_run" and result.get("ok"):
        if result.get("verify_job_state") == "running":
            return PHASE_VERIFY
        return PHASE_COMPLETE
    if tool_name == "apatch_verify_status" and result.get("ok"):
        if result.get("verify_job_state") == "passed":
            return PHASE_COMPLETE
        if result.get("verify_job_state") == "running":
            return PHASE_VERIFY
    if tool_name == "apatch_doctor" and result.get("ok", True):
        return PHASE_PLAN
    if result.get("phase") == "done" and result.get("ok"):
        return PHASE_VERIFY
    if tool_name in {"apatch_resume_session", "apatch_recover"} and result.get("resumed"):
        # A verify failure whose chunk was already rolled back resumes into apply so
        # corrected needles can be written. Other recoveries resume into verify for
        # re-check/attest. Preserve that choice through MCP response enrichment.
        return PHASE_APPLY if result.get("resume_mode") == "reapply" else PHASE_VERIFY
    return TOOL_DEFAULT_PHASE.get(tool_name, PHASE_IDLE)


def _derive_next_action(
    phase: str,
    tool_name: str,
    result: Dict[str, Any],
    failure: Optional[FailureInfo],
    *,
    target_dir: str = ".",
) -> str:
    from apatch.runtime.hints import (
        hint_apply_session,
        hint_attest,
        hint_attestation_export,
        hint_doctor,
        hint_plan_batch,
        hint_rollback,
        hint_session_end,
        hint_session_start,
        hint_verify_run,
    )

    if failure:
        action = failure.recommended_action
        if action == "rollback":
            ckpt = result.get("checkpoint") or result.get("session_id")
            return hint_rollback(ckpt)
        if action == "retry_chunk":
            return f"{hint_apply_session()} — retry after fixing root cause"
        if action == "reduce_scope":
            return "apatch_apply_session(chunk_max_files=3) or apatch_generate with narrower glob"
        return f"inspect failure.error_type={failure.error_type}"

    if result.get("agent_next"):
        return str(result["agent_next"])
    if result.get("next_action"):
        return str(result["next_action"])

    if tool_name == "apatch_session_start" and result.get("ok"):
        return hint_plan_batch()
    if tool_name in (
        "apatch_apply",
        "apatch_strip",
        "apatch_phase_run",
        "apatch_governed_phase_run",
    ) and result.get("ok"):
        return hint_verify_run()
    if tool_name == "apatch_verify_run" and result.get("ok"):
        return hint_attest()
    if tool_name == "apatch_attest" and result.get("ok"):
        from apatch.agent_guidance import hint_trustchain_coverage, primary_artifact_token

        if primary_artifact_token(target_dir):
            return (
                f"{hint_trustchain_coverage(target_dir)} — then {hint_attestation_export()}"
            )
        return hint_attestation_export()
    if tool_name == "apatch_attestation_export" and result.get("ok"):
        return hint_session_end()

    if phase == PHASE_PLAN:
        return f"apatch_generate → {hint_plan_batch()} → {hint_apply_session()}"
    if phase == PHASE_APPLY and result.get("continue"):
        return f"{hint_apply_session()} — same session until continue=false"
    if phase == PHASE_VERIFY:
        return (
            "apatch_verify_notarization(staged=true); "
            f"{hint_verify_run()}; apatch_arch_check; apatch_db_check"
        )
    if phase == PHASE_ARCH:
        return f"fix arch violations or {hint_rollback()}"
    if phase == PHASE_DB:
        return "apatch_db_revision or apatch_db_run manifest"
    if phase == PHASE_COMPLETE:
        return "git commit (hook runs apatch_verify_notarization)"
    if phase == PHASE_IDLE:
        raw = load_session_state(target_dir)
        has_open_session = (
            bool(raw.get("session_id"))
            and bool(raw.get("intent"))
            and not raw.get("ended_at")
        )
        if not has_open_session:
            return hint_session_start()
    return hint_doctor()


def _budget_remaining(result: Dict[str, Any]) -> Optional[int]:
    progress = result.get("progress") or {}
    total = progress.get("chunks_total")
    done = progress.get("chunks_done")
    if total is not None and done is not None:
        return max(0, int(total) - int(done))
    return None


def build_state_update(
    tool_name: str,
    result: Dict[str, Any],
    *,
    failure: Optional[FailureInfo] = None,
    target_dir: str = ".",
) -> Dict[str, Any]:
    if failure is None and result.get("ok") is False:
        failure = classify_failure(result, tool_name)
    phase = _derive_phase(tool_name, result, failure)
    risk = _derive_risk(result, failure)
    update: Dict[str, Any] = {
        "phase": phase,
        "next_action": _derive_next_action(phase, tool_name, result, failure, target_dir=target_dir),
        "risk_level": risk,
        "last_tool": tool_name,
    }
    ckpt = result.get("checkpoint")
    if ckpt:
        update["checkpoint"] = ckpt
    br = _budget_remaining(result)
    if br is not None:
        update["budget_remaining"] = br
    tc = result.get("trustchain")
    if isinstance(tc, dict) and "active" in tc:
        update["trustchain"] = bool(tc.get("active"))
    elif result.get("trustchain_committed") is not None:
        update["trustchain"] = bool(result.get("trustchain_committed"))
    if failure:
        update["failure"] = failure.to_dict()
    return update


def enrich_tool_response(
    tool_name: str,
    result: Any,
    *,
    target_dir: str = ".",
    expected_session_id: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Attach state_update + failure taxonomy; persist session_state.json."""
    if not isinstance(result, dict):
        return {"ok": True, "value": result}

    prev = load_session_state(target_dir)
    actual_session_id = str(prev.get("session_id") or "")
    if (
        expected_session_id is not None
        and actual_session_id != str(expected_session_id)
    ):
        from apatch.runtime.errors import SessionBindingError

        out = SessionBindingError(
            "Governed session changed while the operation was running.",
            error_type="SESSION_MISMATCH",
            expected=expected_session_id,
            actual=actual_session_id or None,
        ).to_dict()
        out["session_state_path"] = session_state_path(target_dir)
        out["session_state_write_behind"] = True
        return out
    if (
        expected_revision is not None
        and int(prev.get("revision") or 0) != int(expected_revision)
    ):
        from apatch.runtime.errors import SessionBindingError

        out = SessionBindingError(
            "Session revision changed while the operation was running.",
            error_type="SESSION_REVISION_MISMATCH",
            expected=str(expected_revision),
            actual=str(prev.get("revision") or 0),
        ).to_dict()
        out["session_state_path"] = session_state_path(target_dir)
        out["session_state_write_behind"] = True
        return out

    # Capability and immutable-log rejections are ownership guards, not work
    # failures. Preserve their precise protocol error and leave lifecycle state unchanged.
    if result.get("error_type") in _SESSION_BINDING_REJECTION_TYPES:
        out = dict(result)
        out["state_update"] = {
            "phase": prev.get("phase", PHASE_IDLE),
            "next_action": (
                result.get("agent_next")
                or result.get("hint")
                or "use the exact governed session capability"
            ),
            "risk_level": RISK_LOW,
            "last_tool": tool_name,
        }
        out["session_state_path"] = session_state_path(target_dir)
        out["session_state_write_behind"] = True
        return out

    # A RUNTIME_TRANSITION is a guard rejection (valid op, wrong lifecycle phase, e.g. attest
    # while still applying), NOT a work failure. It must be a no-op on session_state: never
    # recorded as a `failure` (which would derive lifecycle='failed') nor change the phase, and
    # never reclassified to an APPLY_FAILED 'rollback' (which would discard good applied work).
    if result.get("error_type") == "RUNTIME_TRANSITION":
        prev = load_session_state(target_dir)
        out = dict(result)
        out["state_update"] = {
            "phase": prev.get("phase", "idle"),
            "next_action": result.get("agent_next") or result.get("hint") or "call the operation valid for this lifecycle",
            "risk_level": "low",
            "last_tool": tool_name,
        }
        out["session_state_path"] = session_state_path(target_dir)
        out["session_state_write_behind"] = True
        try:
            from apatch.runtime.session import build_session_view

            view = build_session_view(target_dir)
            out["core_invariant"] = view.get("core_invariant")
            out["invariant"] = view.get("invariant")
        except Exception:
            pass
        return out

    pre_mutation_authoring_failure = (
        tool_name == "apatch_spec_scaffold"
        and result.get("ok") is False
        and not result.get("written")
        and not result.get("mutation_performed")
    )
    session_neutral_operational = (
        tool_name in _SESSION_NEUTRAL_OPERATIONAL_TOOLS
    )
    if (
        tool_name in _READ_ONLY_LIFECYCLE_TOOLS
        or session_neutral_operational
        or pre_mutation_authoring_failure
    ):
        out = dict(result)
        failed_query = result.get("ok") is False or bool(result.get("rejected"))
        failure_next = (
            "resolve the reported operational error and retry"
            if session_neutral_operational
            else "resolve the reported read-only query error and retry"
        )
        out["state_update"] = {
            "phase": prev.get("phase", PHASE_IDLE),
            "next_action": (
                result.get("agent_next")
                or result.get("hint")
                or (
                    failure_next
                    if failed_query
                    else prev.get("next_action") or "continue governed workflow"
                )
            ),
            "risk_level": RISK_LOW,
            "last_tool": tool_name,
        }
        out["session_state_path"] = session_state_path(target_dir)
        out["session_state_write_behind"] = True
        try:
            from apatch.runtime.session import build_session_view

            view = build_session_view(target_dir)
            out["core_invariant"] = view.get("core_invariant")
            out["invariant"] = view.get("invariant")
        except Exception:
            pass
        try:
            from apatch.agent_guidance import attach_artifact_guidance

            attach_artifact_guidance(out, tool_name, target_dir=target_dir)
        except Exception:
            pass
        return out

    # Preflight failures are request diagnostics, not a durable governed lane.
    steps = result.get("steps_completed")
    pre_session_steps = set(steps) if isinstance(steps, list) else set()
    if (
        tool_name in _PRE_SESSION_ORCHESTRATORS
        and result.get("ok") is False
        and not prev.get("session_id")
        and pre_session_steps <= _PRE_SESSION_STEPS
    ):
        failure = classify_failure(result, tool_name)
        out = dict(result)
        out["state_update"] = {
            "phase": PHASE_IDLE,
            "next_action": result.get("agent_next") or result.get("hint") or "fix the preflight error and retry the same request",
            "risk_level": RISK_LOW,
            "last_tool": tool_name,
        }
        if failure:
            out["failure"] = failure.to_dict()
            out.setdefault("error_type", failure.error_type)
            out.setdefault("recoverable", failure.recoverable)
        out["session_state_path"] = session_state_path(target_dir)
        out["session_state_write_behind"] = True
        out["pre_session_failure"] = True
        return out

    chunk_result = result.get("chunk_result")
    if not isinstance(chunk_result, dict):
        chunk_result = {}
    ended_verify_rollback = (
        bool(prev.get("ended_at"))
        and result.get("error_type") == "VERIFY_FAILED"
        and bool(
            result.get("rollback_performed")
            or chunk_result.get("rollback_performed")
        )
    )
    if ended_verify_rollback:
        failure = classify_failure(result, tool_name)
        next_action = (
            result.get("agent_next")
            or "apatch_session_start(intent='<corrected shared maintenance>')"
        )
        state_update = {
            "phase": PHASE_IDLE,
            "next_action": next_action,
            "risk_level": RISK_LOW,
            "last_tool": tool_name,
        }
        out = dict(result)
        if failure:
            out["failure"] = failure.to_dict()
            out["error_type"] = failure.error_type
            out["recoverable"] = failure.recoverable
            out["recommended_action"] = failure.recommended_action

        merged = {
            **prev,
            **state_update,
            "failure": None,
        }
        changed = session_state_changed(prev, merged)
        if changed:
            try:
                save_session_state(
                    target_dir,
                    merged,
                    expected_session_id=prev.get("session_id"),
                    expected_revision=int(prev.get("revision") or 0),
                )
            except Exception as exc:
                from apatch.runtime.errors import SessionBindingError

                if isinstance(exc, SessionBindingError):
                    return {
                        **exc.to_dict(),
                        "state_update": state_update,
                        "session_state_path": session_state_path(target_dir),
                        "session_state_write_behind": True,
                    }
                raise
        out["state_update"] = state_update
        out["session_state_path"] = session_state_path(target_dir)
        out["session_state_write_behind"] = not changed
        try:
            from apatch.runtime.session import build_session_view

            view = build_session_view(target_dir)
            out["core_invariant"] = view.get("core_invariant")
            out["invariant"] = view.get("invariant")
        except Exception:
            pass
        try:
            from apatch.agent_guidance import attach_artifact_guidance

            attach_artifact_guidance(out, tool_name, target_dir=target_dir)
        except Exception:
            pass
        return out

    failure = classify_failure(result, tool_name) if result.get("ok") is False or result.get("rejected") else None
    if failure is None and result.get("verify_rollback"):
        failure = classify_failure(result, tool_name)

    state_update = build_state_update(tool_name, result, failure=failure, target_dir=target_dir)
    out = dict(result)
    out["state_update"] = state_update

    if failure:
        out["failure"] = failure.to_dict()
        out["error_type"] = failure.error_type
        out["recoverable"] = failure.recoverable
        out["recommended_action"] = failure.recommended_action

    merged = {**prev, **state_update}
    if failure:
        merged["failure"] = failure.to_dict()
    elif result.get("ok") is not False:
        merged["failure"] = None
    if state_update.get("trustchain") is not None:
        merged["trustchain"] = state_update["trustchain"]
    if tool_name == "apatch_attest" and result.get("ok"):
        merged["attested"] = True
    elif tool_name == "apatch_session_start" and result.get("ok"):
        merged["attested"] = False
    _state_changed = session_state_changed(prev, merged)
    if _state_changed:
        save_kwargs: Dict[str, Any] = {}
        if expected_session_id is not None:
            save_kwargs["expected_session_id"] = expected_session_id
        if expected_revision is not None:
            save_kwargs["expected_revision"] = expected_revision
        try:
            save_session_state(target_dir, merged, **save_kwargs)
        except Exception as exc:
            from apatch.runtime.errors import SessionBindingError

            if isinstance(exc, SessionBindingError):
                return {
                    **exc.to_dict(),
                    "state_update": state_update,
                    "session_state_path": session_state_path(target_dir),
                    "session_state_write_behind": True,
                }
            raise
    out["session_state_path"] = session_state_path(target_dir)
    out["session_state_write_behind"] = not _state_changed

    try:
        from apatch.runtime.session import build_session_view

        view = build_session_view(target_dir)
        out["core_invariant"] = view.get("core_invariant")
        out["invariant"] = view.get("invariant")
    except Exception:
        pass

    try:
        from apatch.runtime.events import emit_for_tool_result

        emit_for_tool_result(tool_name, result, target_dir, merged, failure)
    except ImportError:
        pass

    try:
        from apatch.agent_guidance import attach_artifact_guidance

        attach_artifact_guidance(out, tool_name, target_dir=target_dir)
    except Exception:
        pass
    return out


def read_session_state_workspace(target_dir: str = ".") -> Dict[str, Any]:
    """Return persisted agent state for MCP/CLI."""
    root = os.path.abspath(target_dir)
    state = load_session_state(root)
    state["session_state_path"] = session_state_path(root)
    state["ok"] = True
    return state
