"""Intent-level orchestration for remote apatch tasks."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from apatch.remote.errors import RemoteTaskError
from apatch.remote.policy import resolve_remote_target
from apatch.remote.target import RemoteTarget
from apatch.remote.transport import RemoteTransport


DEFAULT_APPROVAL_BOUNDARY = "single_intent"
DEFAULT_MAX_APPLY_ITERATIONS = 100


def remote_task_run(
    target: Union[str, RemoteTarget],
    intent: str,
    plan: Optional[Mapping[str, Any]] = None,
    verify: Optional[Union[str, Sequence[str], Mapping[str, Any]]] = None,
    transport: Optional[RemoteTransport] = None,
    policy_root: str = ".",
    policy: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a governed remote task through one high-level approval boundary.

    The orchestrator intentionally hides the internal doctor/session/patch/verify
    chain behind a single function call. UI layers can ask for one intent-level
    approval, while this function produces a structured timeline for audit.
    """

    if transport is None:
        raise RemoteTaskError(
            "REMOTE_TRANSPORT_REQUIRED",
            "A remote transport must be supplied for remote_task_run.",
            recoverable=True,
            recommended_action="Provide an SSH-backed transport or use FakeRemoteTransport in tests.",
        )

    try:
        parsed_target = (
            target
            if isinstance(target, RemoteTarget)
            else resolve_remote_target(str(target), policy_root=policy_root, policy=policy)
        )
    except RemoteTaskError as exc:
        result = _base_result(target, intent)
        result.update(exc.to_result())
        return result

    from apatch.sdd_integrity import SddAdmissionError, admit_session_effect

    try:
        admit_session_effect(
            policy_root,
            {
                "surface": "remote",
                "effect": "run",
                "target": parsed_target.alias or parsed_target.workspace_id,
            },
        )
    except SddAdmissionError as exc:
        result = _base_result(parsed_target, intent)
        result.update(exc.to_dict())
        return result

    normalized_plan = dict(plan or {})
    if "rollback_session" in normalized_plan:
        rollback_session = normalized_plan.get("rollback_session")
        if not isinstance(rollback_session, str) or not rollback_session.strip():
            result = _success_result(parsed_target, intent, [])
            result.update(
                {
                    "ok": False,
                    "failed_step": "preflight",
                    "error_type": "REMOTE_ROLLBACK_REQUIRES_CHECKPOINT",
                    "message": "plan.rollback_session requires a non-empty checkpoint id.",
                    "recoverable": True,
                    "recommended_action": "Pass the checkpoint returned by the failed remote apply_session.",
                }
            )
            return result
    if normalized_plan.get("commit_attested"):
        raw_sessions = normalized_plan.get("session_ids") or normalized_plan.get(
            "governed_session_id"
        )
        if not raw_sessions:
            result = _success_result(parsed_target, intent, [])
            result.update(
                {
                    "ok": False,
                    "failed_step": "preflight",
                    "error_type": "REMOTE_COMMIT_REQUIRES_SESSIONS",
                    "message": "plan.commit_attested requires explicit session_ids.",
                    "recoverable": True,
                    "recommended_action": "Pass the completed governed session ids whose exact files should be committed.",
                }
            )
            return result
    if "defer_finalize" in normalized_plan:
        deferred = normalized_plan["defer_finalize"]
        supported = normalized_plan.get("fix_forward_current") or normalized_plan.get("execute_next")
        if type(deferred) is not bool or (deferred and (
            not supported or normalized_plan.get("finalize_current")
        )):
            return {
                **_success_result(parsed_target, intent, []),
                "ok": False, "failed_step": "preflight",
                "error_type": "REMOTE_DEFER_FINALIZE_INVALID",
                "message": "defer_finalize must be a boolean on execute_next or fix_forward_current.",
            }
    if normalized_plan.get("fix_forward_current"):
        if not verify and not normalized_plan.get("defer_finalize"):
            result = _success_result(parsed_target, intent, [])
            result.update(
                {
                    "ok": False,
                    "failed_step": "preflight",
                    "error_type": "REMOTE_FIX_FORWARD_REQUIRES_VERIFY",
                    "message": "plan.fix_forward_current requires a fresh verify command.",
                    "recoverable": True,
                    "recommended_action": "Pass verify=... so the repaired remote session is verified before attest/session_end.",
                }
            )
            return result
        if not _has_patch_plan(normalized_plan):
            result = _success_result(parsed_target, intent, [])
            result.update(
                {
                    "ok": False,
                    "failed_step": "preflight",
                    "error_type": "REMOTE_FIX_FORWARD_REQUIRES_PATCH",
                    "message": "plan.fix_forward_current requires needles, patches, or logs_path.",
                    "recoverable": True,
                    "recommended_action": "Put the corrective mutation in plan.needles or plan.logs_path.",
                }
            )
            return result
    if normalized_plan.get("finalize_current") and not verify:
        result = _success_result(parsed_target, intent, [])
        result.update(
            {
                "ok": False,
                "failed_step": "preflight",
                "error_type": "REMOTE_FINALIZE_REQUIRES_VERIFY",
                "message": "plan.finalize_current requires a fresh verify command.",
                "recoverable": True,
                "recommended_action": "Pass verify=... so the remote session is re-verified before attest/session_end.",
            }
        )
        return result
    mixed = _detect_mixed_workspace(parsed_target, normalized_plan)
    if mixed:
        result = _success_result(parsed_target, intent, [])
        result.update(
            {
                "ok": False,
                "failed_step": "preflight",
                "error_type": mixed[0],
                "message": mixed[1],
                "recoverable": True,
                "recommended_action": "Use a remote-relative logs/session path (.apatch/...) for remote targets.",
            }
        )
        return result
    steps = _build_steps(intent=intent, plan=normalized_plan, verify=verify)
    timeline: List[Dict[str, Any]] = []
    context: Dict[str, Any] = {}
    max_apply_iterations = _positive_int(
        normalized_plan.get("max_apply_iterations"), DEFAULT_MAX_APPLY_ITERATIONS
    )

    for operation, arguments in steps:
        if not _operation_allowed(parsed_target, operation):
            result = _success_result(parsed_target, intent, timeline)
            result.update(
                _policy_denied_result(operation, parsed_target)
            )
            return result

        merged_arguments = _merge_context(operation, arguments, context)
        apply_iterations = 0
        while True:
            call_arguments = merged_arguments
            response = transport.call(parsed_target, operation, call_arguments)

            # A busy remote session is not evidence of a stale lock. Another agent
            # may be in the middle of a valid governed lifecycle, and closing it here
            # would discard its capability and break transactional ownership. Inspect
            # the authoritative state for diagnostics, then fail closed. Recovery is
            # explicit via fix_forward_current/finalize_current (or reset_session only
            # after the caller has independently proved that the lock is abandoned).
            post_response_steps: List[Dict[str, Any]] = []
            if _is_remote_timeout(response) and _operation_allowed(
                parsed_target, "apatch_session_state"
            ):
                state_args = _merge_context(
                    "apatch_session_state",
                    {
                        "intent": intent,
                        "_auto_recovery": "remote_timeout_reconcile",
                    },
                    context,
                )
                state_response = transport.call(
                    parsed_target,
                    "apatch_session_state",
                    state_args,
                )
                post_response_steps.append(
                    _timeline_step(
                        "apatch_session_state",
                        state_args,
                        _sanitize_for_target(parsed_target, state_response),
                        parsed_target,
                    )
                )
                reconciled = _reconcile_remote_timeout(
                    operation,
                    call_arguments,
                    state_response,
                )
                if reconciled is not None:
                    response = reconciled
                else:
                    response = dict(response)
                    response["recommended_action"] = "reconcile_remote_state"
                    response["remote_state"] = _remote_state_summary(state_response)

            if (
                operation == "apatch_session_start"
                and not response.get("ok", True)
                and _is_active_session_lock(response)
            ):
                active_state: Mapping[str, Any] = {}
                if _operation_allowed(parsed_target, "apatch_session_state"):
                    state_args = {
                        "intent": intent,
                        "_auto_recovery": "active_session_lock_inspection",
                    }
                    active_state = transport.call(
                        parsed_target,
                        "apatch_session_state",
                        state_args,
                    )
                    post_response_steps.append(
                        _timeline_step(
                            "apatch_session_state",
                            state_args,
                            _sanitize_for_target(parsed_target, active_state),
                            parsed_target,
                        )
                    )
                    context["apatch_session_state"] = active_state

                response = dict(response)
                response.update(
                    {
                        "error_type": "REMOTE_SESSION_BUSY",
                        "message": (
                            "A governed remote session is already active; "
                            "remote_task_run did not close or replace it."
                        ),
                        "recoverable": True,
                        "recommended_action": (
                            "Wait for the active task, or inspect apatch_session_state "
                            "and use fix_forward_current/finalize_current for that exact "
                            "session. Use reset_session only after proving it is abandoned."
                        ),
                    }
                )
                session = (
                    active_state.get("session")
                    if isinstance(active_state, Mapping)
                    and isinstance(active_state.get("session"), Mapping)
                    else {}
                )
                if session:
                    response["active_session"] = {
                        key: session.get(key)
                        for key in (
                            "session_id",
                            "lifecycle",
                            "revision",
                            "started_at",
                            "intent",
                        )
                        if session.get(key) is not None
                    }

            # Self-heal a frozen apply session. apply_session returns ok:True but status
            # SESSION_ALREADY_COMPLETE (nothing applied) when a prior run for the same
            # logs_path finished and a stale .apatch/apply_session.json sits at 100%. The
            # autopilot calls apply_session WITHOUT reset=true, so even a fresh session and a
            # different patch see "already complete" and write nothing. Retry ONCE with
            # reset=true so the current patch set actually applies — same self-heal class as
            # the stale-session-lock deadlock above.
            if operation == "apatch_apply_session" and _is_session_already_complete(response):
                reset_args = _with_apply_session_reset(
                    merged_arguments, "stale_apply_session"
                )
                response = transport.call(parsed_target, operation, reset_args)
                call_arguments = reset_args  # reset applies to this recovery call only

            # Self-heal a stale in-progress apply lock for another logs_path. This
            # happens when a previous run died mid-chunk and left
            # .apatch/apply_session.json pointing at old staging logs. A fresh
            # remote_task already has a governed session and a new logs_path, so
            # reset once and apply the current patch instead of handing the agent
            # a misleading failure.
            if operation == "apatch_apply_session" and _is_stale_apply_session_lock(response):
                reset_args = _with_apply_session_reset(
                    merged_arguments, "stale_apply_session_lock"
                )
                response = transport.call(parsed_target, operation, reset_args)
                call_arguments = reset_args  # reset applies to this recovery call only

            if (
                operation == "apatch_verify_run"
                and call_arguments.get("enforce_requirement_verify")
                and response.get("ok")
                and (response.get("requirement_verification") or {}).get("complete") is not True
            ):
                response = {
                    **response, "ok": False,
                    "error_type": "REMOTE_SPEC_VERIFY_UNCONFIRMED",
                    "message": "Worker did not confirm completion of the session's required SPEC verification.",
                    "recoverable": True,
                    "recommended_action": "Update the remote worker and finalize the same session; do not attest.",
                }
            safe_response = _sanitize_for_target(parsed_target, response)
            step = _timeline_step(operation, call_arguments, safe_response, parsed_target)
            timeline.append(step)
            timeline.extend(post_response_steps)
            context[operation] = response

            if not response.get("ok", True):
                cleanup: Optional[Dict[str, Any]] = None
                # generate/simulate cannot mutate source files. If either fails after
                # this orchestrator opened the generic session, close it immediately:
                # there is nothing to roll back, and leaving the capability registered
                # would deadlock the correct SPEC-bound retry behind an active session.
                generic_pre_mutation = (
                    operation in {"apatch_generate_batch", "apatch_simulate"}
                    and "apatch_session_start" in context
                    and "apatch_apply_session" not in context
                    and safe_response.get("error_type") != "REMOTE_TIMEOUT"
                )
                execute_steps = set(response.get("steps_completed") or [])
                targeted_pre_mutation = (
                    operation == "apatch_execute_next"
                    and "session_start" in execute_steps
                    and "apply_session" not in execute_steps
                    and "session_end" not in execute_steps
                )
                cleanup_reason = (
                    "execute_next_pre_mutation_failure"
                    if targeted_pre_mutation
                    else "pre_mutation_failure"
                )
                if (
                    (generic_pre_mutation or targeted_pre_mutation)
                    and _operation_allowed(parsed_target, "apatch_session_end")
                ):
                    cleanup_args = _merge_context(
                        "apatch_session_end",
                        {
                            "intent": intent,
                            "_auto_recovery": cleanup_reason,
                        },
                        context,
                    )
                    cleanup_response = transport.call(
                        parsed_target,
                        "apatch_session_end",
                        cleanup_args,
                    )
                    safe_cleanup = _sanitize_for_target(parsed_target, cleanup_response)
                    timeline.append(
                        _timeline_step(
                            "apatch_session_end",
                            cleanup_args,
                            safe_cleanup,
                            parsed_target,
                        )
                    )
                    context["apatch_session_end"] = cleanup_response
                    cleanup = {
                        "attempted": True,
                        "ok": cleanup_response.get("ok", True),
                        "reason": cleanup_reason,
                    }

                result = _success_result(parsed_target, intent, timeline)
                result.update(
                    {
                        "ok": False,
                        "failed_step": operation,
                        "error_type": safe_response.get("error_type", "REMOTE_TASK_FAILED"),
                        "message": safe_response.get("message", "Remote task step failed."),
                        "recoverable": safe_response.get("recoverable", True),
                    }
                )
                if safe_response.get("recommended_action"):
                    result["recommended_action"] = safe_response["recommended_action"]
                if safe_response.get("active_session"):
                    result["active_session"] = safe_response["active_session"]
                if cleanup is not None:
                    result["session_cleanup"] = cleanup
                return result

            if operation != "apatch_apply_session" or not response.get("continue"):
                break

            apply_iterations += 1
            if apply_iterations >= max_apply_iterations:
                result = _success_result(parsed_target, intent, timeline)
                result.update(
                    {
                        "ok": False,
                        "failed_step": operation,
                        "error_type": "REMOTE_APPLY_SESSION_DID_NOT_CONVERGE",
                        "message": "Remote apply_session kept returning continue=true.",
                        "recoverable": True,
                        "recommended_action": "Inspect remote .apatch/apply_session.json or lower chunk size before retrying.",
                    }
                )
                return result

    result = _success_result(parsed_target, intent, timeline)
    recovered_result = next(
        (
            step.get("result")
            for step in reversed(timeline)
            if isinstance(step.get("result"), Mapping)
            and step["result"].get("recovered_from") == "REMOTE_TIMEOUT"
        ),
        None,
    )
    result["last_result"] = recovered_result or (timeline[-1]["result"] if timeline else {})
    if normalized_plan.get("defer_finalize"):
        result["finalize_deferred"] = True
        result["finalized"] = False
        result["agent_next"] = (
            "Keep this session open. After the required service action, "
            "use finalize_current to run mandatory SPEC verification, attest, and close."
        )
    return result


def _is_remote_timeout(response: Mapping[str, Any]) -> bool:
    return (
        response.get("ok") is False
        and str(response.get("error_type") or "") == "REMOTE_TIMEOUT"
    )


def _remote_state_summary(response: Mapping[str, Any]) -> Dict[str, Any]:
    session = response.get("session")
    if not isinstance(session, Mapping):
        session = response
    return {
        key: session.get(key)
        for key in (
            "session_id",
            "lifecycle",
            "phase",
            "last_tool",
            "next_action",
            "ended_at",
            "failure",
        )
        if session.get(key) is not None
    }


def _reconcile_remote_timeout(
    operation: str,
    arguments: Mapping[str, Any],
    state_response: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Treat a timeout as success only when remote state proves this exact work ended."""

    if state_response.get("ok") is False:
        return None
    session = state_response.get("session")
    if not isinstance(session, Mapping) or session.get("failure"):
        return None

    expected_session_id = str(arguments.get("governed_session_id") or "").strip()
    actual_session_id = str(session.get("session_id") or "").strip()
    if expected_session_id and actual_session_id != expected_session_id:
        return None

    raw_plan = arguments.get("plan")
    plan = dict(raw_plan) if isinstance(raw_plan, Mapping) else {}
    identities: List[str] = []
    requirement = str(plan.get("requirement") or "").strip()
    spec = str(plan.get("spec") or "").strip()
    if requirement:
        identities.append(requirement)
    elif spec:
        identities.append(spec)
    if plan.get("specs"):
        return None
    if identities:
        artifact_blob = json.dumps(
            session.get("artifacts") or [],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if not all(identity in artifact_blob for identity in identities):
            return None

    phase = str(session.get("phase") or "").strip().lower()
    lifecycle = str(session.get("lifecycle") or "").strip().lower()
    last_tool = str(session.get("last_tool") or "").strip()
    ended = bool(session.get("ended_at")) or lifecycle == "ended"
    task_operations = {
        "apatch_execute_next",
        "apatch_spec_run",
        "apatch_slug_ratify",
        "apatch_rebind_stale",
    }
    if operation in task_operations:
        proven = bool(identities) and ended and phase in {"complete", "idle"}
    elif operation == "apatch_session_end":
        proven = ended
    else:
        proven = last_tool == operation and phase in {
            "plan",
            "apply",
            "verify",
            "complete",
            "idle",
        }
    if not proven:
        return None

    recovered: Dict[str, Any] = {
        "ok": True,
        "reconciled": True,
        "recovered_from": "REMOTE_TIMEOUT",
        "operation": operation,
        "remote_state": _remote_state_summary(state_response),
    }
    if operation == "apatch_execute_next" and _has_patch_plan(plan):
        recovered["applied"] = 1
        recovered["applied_reconciled"] = True
    return recovered


def _detect_mixed_workspace(
    target: RemoteTarget, plan: Mapping[str, Any]
) -> Optional[Tuple[str, str]]:
    """Reject a plan that mixes a local absolute path into a remote apply.

    Remote governed state lives under the remote repo's ``.apatch``; a local
    absolute logs/session path almost always means the controller and executor
    workspaces were crossed. Fail closed before any remote step runs.
    """

    for key in ("logs_path", "session_path", "out_path"):
        value = plan.get(key)
        if (
            isinstance(value, str)
            and value.startswith("/")
            and not value.startswith(target.path)
        ):
            return (
                "WORKSPACE_MISMATCH",
                "A local absolute {} was supplied for a remote apply.".format(key),
            )
    return None


def _is_active_session_lock(response: Mapping[str, Any]) -> bool:
    """True when session_start refused because a prior session is still open."""
    return str(response.get("error") or "").strip().lower() == "active session exists"


def _is_session_already_complete(response: Mapping[str, Any]) -> bool:
    """True when apply_session returned ok but applied nothing because a prior session
    for the same logs_path is frozen at 100% (a stale .apatch/apply_session.json)."""
    return str(response.get("status") or "").strip().upper() == "SESSION_ALREADY_COMPLETE"


def _is_stale_apply_session_lock(response: Mapping[str, Any]) -> bool:
    """True when a stale apply_session.json blocks a fresh logs_path."""
    text = str(response.get("error") or response.get("message") or "").strip().lower()
    return text.startswith("apply_session: in-progress session for ")


def _with_apply_session_reset(arguments: Mapping[str, Any], reason: str) -> Dict[str, Any]:
    reset_args = dict(arguments)
    reset_plan = dict(reset_args.get("plan") or {})
    reset_plan["reset"] = True
    reset_args["plan"] = reset_plan
    reset_args["reset"] = True
    reset_args["_auto_recovery"] = reason
    return reset_args


def _operation_allowed(target: RemoteTarget, operation: str) -> bool:
    """Mirror the loop's policy gate so self-heal respects allowed_operations."""
    return target.allowed_operations is None or operation in target.allowed_operations


def _build_steps(
    intent: str,
    plan: Mapping[str, Any],
    verify: Optional[Union[str, Sequence[str], Mapping[str, Any]]],
) -> List[Tuple[str, Dict[str, Any]]]:
    if plan.get("commit_attested"):
        commit_plan = dict(plan)
        commit_plan["message"] = str(commit_plan.get("message") or intent).strip()
        return [
            ("apatch_doctor", {}),
            (
                "apatch_commit_attested",
                {"plan": commit_plan, "message": commit_plan["message"]},
            ),
        ]

    if plan.get("rollback_session"):
        return [
            ("apatch_doctor", {}),
            ("apatch_rollback", {"session_id": str(plan["rollback_session"])}),
            ("apatch_session_end", {"intent": intent}),
        ]

    if plan.get("fix_forward_current"):
        # Repair the active failed remote session in place. reset=True discards the
        # completed/failed apply cursor and moves the resumed verifying lifecycle
        # back to apply before the corrective patch is written.
        fix_plan = dict(plan)
        fix_plan["reset"] = True
        fix_plan["verify_deferred"] = True
        steps: List[Tuple[str, Dict[str, Any]]] = [
            ("apatch_doctor", {}),
            ("apatch_resume_session", {}),
        ]
        if _requires_generate_batch(fix_plan):
            steps.append(("apatch_generate_batch", {"plan": fix_plan}))
        steps.extend(
            [
                ("apatch_simulate", {"plan": fix_plan}),
                ("apatch_apply_session", {"plan": fix_plan}),
            ]
        )
        if not plan.get("defer_finalize"):
            steps.extend([
                ("apatch_verify_run", {
                    "verify": _normalize_verify(verify), "enforce_requirement_verify": True,
                }),
                ("apatch_attest", {"intent": intent}),
                ("apatch_session_end", {"intent": intent}),
            ])
        return steps

    if plan.get("finalize_current"):
        # Close the active remote session after a transient verify failure without
        # opening a new governed session or staging a no-op mutation. This is the
        # normal recovery path for "verify failed because service restart was slow;
        # the same gate is green now".
        steps: List[Tuple[str, Dict[str, Any]]] = [
            ("apatch_doctor", {}),
            ("apatch_resume_session", {}),
        ]
        if verify:
            steps.append(("apatch_verify_run", {
                "verify": _normalize_verify(verify), "enforce_requirement_verify": True,
            }))
        steps.extend(
            [
                ("apatch_attest", {"intent": intent}),
                ("apatch_session_end", {"intent": intent}),
            ]
        )
        return steps

    if plan.get("reset_session"):
        # Explicit emergency lever (pairs with the auto self-heal in the run loop):
        # clear a stale governed lock without running a full mutation pipeline.
        # Gives the agent — and doctor/status recommendations — a recovery path
        # that does not depend on a hung verify ever completing.
        return [
            ("apatch_doctor", {}),
            ("apatch_session_end", {"intent": intent}),
        ]
    if plan.get("rebind_stale"):
        # Shared-file staleness is an attestation repair, not a source mutation.
        # The remote worker owns verify + batch rebind without a generic session.
        return [
            ("apatch_doctor", {}),
            ("apatch_rebind_stale", {"plan": dict(plan)}),
        ]

    if plan.get("execute_next"):
        # Targeted re-mutation of one exact SPEC requirement. The worker owns the
        # execute_next mutate + finalize cycle, so the ordinary generic session
        # must not wrap it.
        return [
            ("apatch_doctor", {}),
            ("apatch_execute_next", {"plan": dict(plan)}),
        ]

    if plan.get("slug_ratify"):
        # Slug ratification owns verify-once, batch re-attestation, and the
        # conformance gate. Route it directly without an outer generic session.
        return [
            ("apatch_doctor", {}),
            ("apatch_slug_ratify", {"plan": dict(plan)}),
        ]

    single_spec = _single_spec_id(plan)
    if single_spec:
        # A single spec owns its per-Rk lifecycle just like spec_run_multi. Route
        # it directly instead of forcing callers to invent a no-op peer spec.
        spec_plan = dict(plan)
        spec_plan["spec"] = single_spec
        return [
            ("apatch_doctor", {}),
            ("apatch_spec_run", {"plan": spec_plan}),
        ]
    if _has_spec_run_multi_plan(plan):
        # spec_run_multi owns its per-Rk sessions, verify, attestation, rollback,
        # and session cleanup. Wrapping it in the ordinary remote session would
        # break exact SPEC ownership, so route it as one governed worker step.
        return [
            ("apatch_doctor", {}),
            ("apatch_spec_run_multi", {"plan": dict(plan)}),
        ]
    steps: List[Tuple[str, Dict[str, Any]]] = [
        ("apatch_doctor", {}),
    ]
    session_args: Dict[str, Any] = {"intent": intent}
    if plan.get("artifacts"):
        session_args["artifacts"] = plan["artifacts"]
    bootstrap_requirement = _bootstrap_spec_requirement(plan)
    if bootstrap_requirement:
        artifacts = list(session_args.get("artifacts") or [])
        # Authorize creation of the SPEC file without claiming that its first
        # requirement was implemented. spec_status only derives completion from
        # kind=spec, while spec_ownership accepts this bootstrap-only capability.
        artifacts.append(f"spec-bootstrap:{bootstrap_requirement}")
        session_args["artifacts"] = artifacts
    elif plan.get("requirement"):
        session_args["requirement"] = plan["requirement"]
        if plan.get("spec_path"):
            session_args["spec_path"] = plan["spec_path"]
    steps.append(("apatch_session_start", session_args))

    if _has_patch_plan(plan):
        if _requires_generate_batch(plan):
            steps.append(("apatch_generate_batch", {"plan": dict(plan)}))
        steps.extend(
            [
                ("apatch_simulate", {"plan": dict(plan)}),
                ("apatch_apply_session", {"plan": dict(plan)}),
            ]
        )

    if plan.get("noop_covered_by"):
        steps.append(("apatch_resume_session", {}))

    if verify:
        steps.append(("apatch_verify_run", {"verify": _normalize_verify(verify)}))

    if plan.get("noop_covered_by"):
        steps.extend(
            [
                (
                    "apatch_noop_attest",
                    {
                        "covered_by": _normalize_covered_by(plan.get("noop_covered_by")),
                        "message": intent,
                    },
                ),
                ("apatch_session_end", {"intent": intent}),
            ]
        )
        return steps

    steps.extend(
        [
            ("apatch_attest", {"intent": intent}),
            ("apatch_session_end", {"intent": intent}),
        ]
    )
    return steps


def _bootstrap_spec_requirement(plan: Mapping[str, Any]) -> Optional[str]:
    """Return an exact requirement only for a self-declaring SPEC create needle."""
    requirement = str(plan.get("requirement") or "").strip()
    if "#" not in requirement:
        return None
    spec_id, req_id = (part.strip() for part in requirement.split("#", 1))
    if not spec_id.startswith("SPEC-") or not req_id:
        return None
    expected_path = f"docs/specs/{spec_id}.md"
    for needle in plan.get("needles") or []:
        if not isinstance(needle, Mapping):
            continue
        action = str(needle.get("action") or "replace").lower()
        target = str(needle.get("target_file") or "")
        content = str(needle.get("content") or "")
        if action != "create" or target != expected_path:
            continue
        if (
            f"# {spec_id}" in content
            and f"spec:{spec_id}" in content
            and f"## {req_id} " in content
        ):
            return requirement
    return None


def _single_spec_id(plan: Mapping[str, Any]) -> Optional[str]:
    explicit = str(plan.get("spec") or "").strip()
    if explicit.startswith("SPEC-"):
        return explicit
    specs = plan.get("specs")
    if isinstance(specs, (list, tuple)) and len(specs) == 1:
        candidate = str(specs[0] or "").strip()
        return candidate if candidate.startswith("SPEC-") else None
    requirements = plan.get("requirements")
    if isinstance(requirements, Mapping):
        spec_keys = [str(key) for key in requirements if str(key).startswith("SPEC-")]
        if len(spec_keys) == 1:
            return spec_keys[0]
    return None


def _has_spec_run_multi_plan(plan: Mapping[str, Any]) -> bool:
    specs = plan.get("specs")
    if isinstance(specs, (list, tuple)) and len(specs) >= 2:
        return True
    requirements = plan.get("requirements")
    if isinstance(requirements, Mapping):
        return len([key for key in requirements if str(key).startswith("SPEC-")]) >= 2
    return False


def _has_patch_plan(plan: Mapping[str, Any]) -> bool:
    return bool(
        plan.get("needles")
        or plan.get("patches")
        or plan.get("logs_path")
        or plan.get("slug_close")
        or plan.get("candidate")
        or plan.get("candidates")
    )


def _requires_generate_batch(plan: Mapping[str, Any]) -> bool:
    return bool(
        plan.get("needles")
        or plan.get("patches")
        or plan.get("slug_close")
        or plan.get("candidate")
        or plan.get("candidates")
    )


def _normalize_covered_by(value: Any) -> List[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _normalize_verify(verify: Union[str, Sequence[str], Mapping[str, Any]]) -> Dict[str, Any]:
    if isinstance(verify, str):
        return {"command": verify}
    if isinstance(verify, Mapping):
        return dict(verify)
    return {"command": list(verify)}


def _merge_context(
    operation: str,
    arguments: Mapping[str, Any],
    context: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    merged = dict(arguments)
    started = context.get("apatch_session_start", {})
    session = started.get("session") if isinstance(started.get("session"), Mapping) else {}
    capability = (
        started.get("session_capability")
        if isinstance(started.get("session_capability"), Mapping)
        else {}
    )
    session_id = str(
        session.get("session_id")
        or capability.get("session_id")
        or started.get("session_id")
        or ""
    ).strip()
    session_token = started.get("session_token") or capability.get("session_token")

    resumed = context.get("apatch_resume_session", {})
    resumed_capability = (
        resumed.get("session_capability")
        if isinstance(resumed.get("session_capability"), Mapping)
        else {}
    )
    resumed_session_id = str(
        resumed_capability.get("session_id")
        or resumed.get("session_id")
        or ""
    ).strip()
    resumed_session_token = (
        resumed.get("session_token")
        or resumed_capability.get("session_token")
    )
    # resume_session rotates the capability; once present it is authoritative.
    # Reusing the session_start token after a resume causes a deterministic
    # SESSION_TOKEN_MISMATCH on verify/noop-attest/session-end.
    active_session_id = resumed_session_id or session_id
    active_session_token = resumed_session_token or session_token
    path_session_id = active_session_id

    if operation not in {"apatch_doctor", "apatch_session_start"}:
        if active_session_id:
            merged["governed_session_id"] = active_session_id
        if active_session_token:
            merged["session_token"] = active_session_token

    raw_plan = merged.get("plan")
    plan = dict(raw_plan) if isinstance(raw_plan, Mapping) else {}
    if path_session_id and operation == "apatch_generate_batch" and not plan.get("out_path"):
        plan["out_path"] = f".apatch/tmp/{path_session_id}/remote-patches.jsonl"
    if path_session_id and operation == "apatch_apply_session" and not plan.get("session_path"):
        plan["session_path"] = f".apatch/tmp/{path_session_id}/remote-apply-session.json"
    if plan:
        merged["plan"] = plan

    generated = context.get("apatch_generate_batch", {})
    if operation in {"apatch_simulate", "apatch_apply_session"}:
        logs_path = (
            generated.get("logs_path")
            or generated.get("out_path_rel")
            or generated.get("out_path")
            or generated.get("out")
            or plan.get("logs_path")
        )
        if logs_path:
            merged["logs_path"] = logs_path

    if operation == "apatch_session_end":
        applied = context.get("apatch_apply_session", {})
        apply_state = applied.get("session_path")
        if apply_state:
            merged["session_path"] = apply_state
    return merged


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _needle_payload_summary(needles: Sequence[Any]) -> Dict[str, Any]:
    """Describe a mutation batch without echoing source/replacement bodies."""
    encoded = json.dumps(
        list(needles),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    actions: set[str] = set()
    targets: set[str] = set()
    for raw in needles:
        if not isinstance(raw, Mapping):
            continue
        actions.add(str(raw.get("action") or "replace"))
        for key in ("target_file", "source_file"):
            value = str(raw.get(key) or "").strip()
            if value:
                targets.add(value)
    return {
        "count": len(needles),
        "actions": sorted(actions),
        "target_files": sorted(targets),
        "payload_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _compact_timeline_arguments(value: Any) -> Any:
    """Bound audit timelines while retaining deterministic plan identity."""
    if isinstance(value, Mapping):
        out: Dict[Any, Any] = {}
        for key, nested in value.items():
            if str(key) == "needles" and isinstance(nested, list):
                out["needles_summary"] = _needle_payload_summary(nested)
            else:
                out[key] = _compact_timeline_arguments(nested)
        return out
    if isinstance(value, list):
        return [_compact_timeline_arguments(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_compact_timeline_arguments(item) for item in value)
    return value


def _timeline_step(
    operation: str,
    arguments: Mapping[str, Any],
    response: Mapping[str, Any],
    target: RemoteTarget,
) -> Dict[str, Any]:
    result = _compact_timeline_result(operation, dict(response))
    safe_arguments = _sanitize_for_target(target, dict(arguments))
    return {
        "operation": operation,
        "arguments": _compact_timeline_arguments(safe_arguments),
        "ok": response.get("ok", True),
        "result": _sanitize_for_target(target, result),
    }


def _compact_timeline_result(operation: str, response: Dict[str, Any]) -> Dict[str, Any]:
    """Keep remote timelines useful without embedding full doctor playbooks.

    The full remote doctor payload can be tens of KB because it includes every
    consumer playbook. That is valuable for onboarding, but remote_task_run is a
    hot path: agents need identity, policy posture, and failure hints, not the
    entire handbook on every mutation.
    """
    if operation != "apatch_doctor":
        return response

    out: Dict[str, Any] = {}
    for key in (
        "ok",
        "version",
        "git_head",
        "workspace",
        "mcp_bound_workspace",
        "host",
        "error_type",
        "message",
        "recommended_action",
    ):
        if key in response:
            out[key] = response[key]

    mcp_health = response.get("mcp_health") or {}
    if isinstance(mcp_health, Mapping):
        catalog = mcp_health.get("mcp_tool_catalog") or response.get("mcp_tool_catalog") or {}
        out["mcp_health"] = {
            "ok": mcp_health.get("ok"),
            "mcp_extra_installed": mcp_health.get("mcp_extra_installed"),
            "tool_count": catalog.get("count") if isinstance(catalog, Mapping) else None,
            "fingerprint": catalog.get("fingerprint") if isinstance(catalog, Mapping) else None,
        }

    trustchain = response.get("trustchain") or {}
    if isinstance(trustchain, Mapping):
        out["trustchain"] = {
            "active": trustchain.get("active"),
            "mode": trustchain.get("mode"),
            "path": trustchain.get("path"),
            "enforcement_active": trustchain.get("enforcement_active"),
        }

    hygiene = response.get("hygiene") or {}
    if isinstance(hygiene, Mapping):
        out["hygiene"] = {
            "status": hygiene.get("status"),
            "orphan_count": hygiene.get("orphan_count"),
            "inferred_count": hygiene.get("inferred_count"),
            "unclassified_count": hygiene.get("unclassified_count"),
            "issues": list(hygiene.get("issues") or [])[:5],
            "gc_recommendation": hygiene.get("gc_recommendation"),
        }

    warnings = response.get("warnings") or []
    if warnings:
        out["warnings"] = list(warnings)[:5]
    out["details_truncated"] = True
    out["full_details_hint"] = "Run apatch_doctor on the remote workspace for the full playbook payload."
    return out


def _timeline_summary(timeline: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    operations = [str(step.get("operation") or "?") for step in timeline]
    failed = next((step for step in timeline if step.get("ok") is False), None)
    return {
        "steps": len(timeline),
        "operations": operations,
        "last_operation": operations[-1] if operations else None,
        "last_ok": timeline[-1].get("ok") if timeline else None,
        "apply_steps": sum(
            1
            for op in operations
            if op in {"apatch_apply_session", "apatch_execute_next", "apatch_spec_run", "apatch_spec_run_multi", "apatch_slug_ratify"}
        ),
        "verify_steps": sum(1 for op in operations if op == "apatch_verify_run"),
        "failed_step": failed.get("operation") if failed else None,
    }


def _success_result(
    target: RemoteTarget,
    intent: str,
    timeline: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    timeline_list = list(timeline)
    return {
        "ok": True,
        "approval_boundary": DEFAULT_APPROVAL_BOUNDARY,
        "approval_prompts_expected": 1,
        "target": target.as_dict(),
        "intent": intent,
        "timeline": timeline_list,
        "summary": _timeline_summary(timeline_list),
    }


def _base_result(target: Any, intent: str) -> Dict[str, Any]:
    return {
        "approval_boundary": DEFAULT_APPROVAL_BOUNDARY,
        "approval_prompts_expected": 1,
        "target": target,
        "intent": intent,
        "timeline": [],
    }


def _policy_denied_result(operation: str, target: RemoteTarget) -> Dict[str, Any]:
    return {
        "ok": False,
        "failed_step": operation,
        "error_type": "REMOTE_OPERATION_DENIED",
        "message": "Remote policy does not allow this operation.",
        "recoverable": True,
        "recommended_action": "Use an alias whose allowed_operations include the requested step.",
        "target": target.as_dict(),
    }


def _sanitize_for_target(target: RemoteTarget, value: Any) -> Any:
    label = target.display or target.alias or "remote"
    if isinstance(value, Mapping):
        redacted: Dict[Any, Any] = {}
        for key, item in value.items():
            key_lower = str(key).lower()
            if key_lower in {"session_token"}:
                redacted[key] = "***REDACTED***"
            elif target.redact and key_lower in {
                "host",
                "remote_host",
                "path",
                "remote_root",
                "root",
                "uri",
                "workspace",
                "target_dir",
            }:
                redacted[key] = label
            else:
                redacted[key] = _sanitize_for_target(target, item)
        return redacted
    if isinstance(value, list):
        return [_sanitize_for_target(target, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_for_target(target, item) for item in value)
    if isinstance(value, str) and target.redact:
        out = value
        for needle in (target.uri, target.path, target.host):
            if needle:
                out = out.replace(needle, label)
        return out
    return value
