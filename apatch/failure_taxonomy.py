"""R56 — formal failure classification for agent orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

ERROR_VERIFY_FAILED = "VERIFY_FAILED"
ERROR_ARCH_VIOLATION = "ARCH_VIOLATION"
ERROR_DB_RISK = "DB_RISK"
ERROR_TRUSTCHAIN_REJECTED = "TRUSTCHAIN_REJECTED"
ERROR_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
ERROR_NOTARIZATION_FAILED = "NOTARIZATION_FAILED"
ERROR_APPLY_FAILED = "APPLY_FAILED"
ERROR_SESSION_ABORTED = "SESSION_ABORTED"
ERROR_MASS_APPLY_BLOCKED = "MASS_APPLY_BLOCKED"
ERROR_REMOTE_TIMEOUT = "REMOTE_TIMEOUT"
ERROR_UNKNOWN = "UNKNOWN"
ERROR_DIRECT_WRITE_BLOCKED = "DIRECT_WRITE_BLOCKED"
ERROR_LEASE_EXPIRED = "LEASE_EXPIRED"
ERROR_LEASE_CONFLICT = "LEASE_CONFLICT"
ERROR_SPEC_DEPENDENCY_UNMET = "SPEC_DEPENDENCY_UNMET"
ERROR_MANIFEST_GAP = "MANIFEST_GAP"
ERROR_MANIFEST_DRIFT = "MANIFEST_DRIFT"
ERROR_SPEC_RUN_BLOCKED = "SPEC_RUN_BLOCKED"
ERROR_SPEC_INTERFERENCE_STALE = "SPEC_INTERFERENCE_STALE"
ERROR_SPEC_SCHEDULE_BLOCKED = "SPEC_SCHEDULE_BLOCKED"
ERROR_SPEC_RUN_ORDER_BLOCKED = "SPEC_RUN_ORDER_BLOCKED"
ERROR_SPEC_WORKFLOW_REQUIRED = "SPEC_WORKFLOW_REQUIRED"

RECOVERABLE_DEFAULTS = {
    ERROR_VERIFY_FAILED: True,
    ERROR_ARCH_VIOLATION: True,
    ERROR_DB_RISK: True,
    ERROR_TRUSTCHAIN_REJECTED: False,
    ERROR_BUDGET_EXCEEDED: True,
    ERROR_NOTARIZATION_FAILED: True,
    ERROR_APPLY_FAILED: True,
    ERROR_SESSION_ABORTED: True,
    ERROR_MASS_APPLY_BLOCKED: True,
    ERROR_UNKNOWN: True,
    ERROR_REMOTE_TIMEOUT: True,
    ERROR_DIRECT_WRITE_BLOCKED: True,
    ERROR_LEASE_EXPIRED: True,
    ERROR_LEASE_CONFLICT: True,
    ERROR_SPEC_DEPENDENCY_UNMET: True,
    ERROR_MANIFEST_GAP: True,
    ERROR_MANIFEST_DRIFT: True,
    ERROR_SPEC_RUN_BLOCKED: True,
    ERROR_SPEC_INTERFERENCE_STALE: True,
    ERROR_SPEC_SCHEDULE_BLOCKED: True,
    ERROR_SPEC_RUN_ORDER_BLOCKED: True,
    ERROR_SPEC_WORKFLOW_REQUIRED: True,
}

ACTION_FIX_FORWARD = "fix_forward"
ACTION_ROLLBACK = "rollback"

RECOMMENDED_ACTION = {
    ERROR_VERIFY_FAILED: ACTION_FIX_FORWARD,
    ERROR_ARCH_VIOLATION: "reduce_scope",
    ERROR_DB_RISK: "reduce_scope",
    ERROR_TRUSTCHAIN_REJECTED: "retry_chunk",
    ERROR_BUDGET_EXCEEDED: "reduce_scope",
    ERROR_NOTARIZATION_FAILED: "rollback",
    ERROR_APPLY_FAILED: "rollback",
    ERROR_SESSION_ABORTED: "retry_chunk",
    ERROR_MASS_APPLY_BLOCKED: "reduce_scope",
    ERROR_UNKNOWN: "rollback",
    ERROR_REMOTE_TIMEOUT: "reconcile_remote_state",
    ERROR_DIRECT_WRITE_BLOCKED: "retry_chunk",
    ERROR_LEASE_EXPIRED: "retry_chunk",
    ERROR_LEASE_CONFLICT: "retry_chunk",
    ERROR_SPEC_DEPENDENCY_UNMET: "reduce_scope",
    ERROR_MANIFEST_GAP: "reduce_scope",
    ERROR_MANIFEST_DRIFT: "retry_chunk",
    ERROR_SPEC_RUN_BLOCKED: "rollback",
    ERROR_SPEC_INTERFERENCE_STALE: "re_run_interference",
    ERROR_SPEC_SCHEDULE_BLOCKED: "resolve_conflicts",
    ERROR_SPEC_RUN_ORDER_BLOCKED: "complete_predecessor_first",
    ERROR_SPEC_WORKFLOW_REQUIRED: "use_spec_workflow",
}


@dataclass
class FailureInfo:
    error_type: str
    recoverable: bool
    recommended_action: str
    message: str
    tool: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _failure(
    error_type: str,
    message: str,
    *,
    tool: Optional[str] = None,
    recoverable: Optional[bool] = None,
    recommended_action: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> FailureInfo:
    return FailureInfo(
        error_type=error_type,
        recoverable=recoverable if recoverable is not None else RECOVERABLE_DEFAULTS.get(error_type, True),
        recommended_action=recommended_action or RECOMMENDED_ACTION.get(error_type, "rollback"),
        message=message,
        tool=tool,
        details=details,
    )



def _verify_recommended_action(result: Dict[str, Any]) -> str:
    chunk = result.get("chunk_result") or {}
    if result.get("rollback_performed") or chunk.get("rollback_performed"):
        return ACTION_FIX_FORWARD
    if result.get("verify_rollback") or chunk.get("verify_rollback"):
        return ACTION_ROLLBACK
    return ACTION_FIX_FORWARD


def _verify_failure_details(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    details: Dict[str, Any] = {}
    chunk = result.get("chunk_result") or {}
    if result.get("verify_rollback") or chunk.get("verify_rollback"):
        details["verify_rollback"] = True
    if result.get("rollback_performed") or chunk.get("rollback_performed"):
        details["rollback_performed"] = True
    baseline = result.get("baseline")
    if isinstance(baseline, dict):
        subset = {
            k: baseline[k]
            for k in ("new_failures", "pre_existing_failures", "unparsed_output", "mode")
            if k in baseline
        }
        if baseline.get("unparsed_output"):
            subset["unparsed_output"] = True
        if subset:
            details["baseline"] = subset
    return details or None

def classify_failure(result: Dict[str, Any], tool_name: str) -> Optional[FailureInfo]:
    """Map tool JSON response to a formal failure record (None if ok)."""
    if result.get("ok") is not False and not result.get("rejected"):
        if result.get("verify_rollback"):
            return _failure(
                ERROR_VERIFY_FAILED,
                "Verification failed; session rolled back.",
                tool=tool_name,
                recommended_action=ACTION_ROLLBACK,
                details={"verify_rollback": True},
            )
        return None

    explicit = result.get("error_type") or ""
    if explicit == ERROR_REMOTE_TIMEOUT:
        return _failure(
            ERROR_REMOTE_TIMEOUT,
            str(result.get("message") or result.get("error") or "Remote operation timed out."),
            tool=tool_name,
            recoverable=result.get("recoverable"),
            recommended_action=result.get("recommended_action")
            or RECOMMENDED_ACTION[ERROR_REMOTE_TIMEOUT],
            details=result.get("remote_state") or result.get("details"),
        )

    if explicit == ERROR_VERIFY_FAILED:
        return _failure(
            ERROR_VERIFY_FAILED,
            str(result.get("error") or "Verify step failed."),
            tool=tool_name,
            recommended_action=result.get("recommended_action")
            or _verify_recommended_action(result),
            details=_verify_failure_details(result),
        )
    if explicit in (
        ERROR_MANIFEST_GAP,
        ERROR_MANIFEST_DRIFT,
        ERROR_SPEC_RUN_BLOCKED,
        ERROR_SPEC_INTERFERENCE_STALE,
        ERROR_SPEC_SCHEDULE_BLOCKED,
        ERROR_SPEC_RUN_ORDER_BLOCKED,
        ERROR_SPEC_WORKFLOW_REQUIRED,
    ):
        return _failure(
            explicit,
            str(result.get("error") or explicit),
            tool=tool_name,
            recommended_action=result.get("recommended_action") or RECOMMENDED_ACTION.get(explicit),
            details={
                k: result[k]
                for k in (
                    "blocked_by",
                    "interference",
                    "schedule",
                    "cross_verify",
                    "requirement_token",
                    "current_requirement",
                    "steps_completed",
                )
                if k in result
            },
        )

    if explicit == ERROR_SPEC_DEPENDENCY_UNMET:
        return _failure(
            ERROR_SPEC_DEPENDENCY_UNMET,
            str(result.get("error") or "Upstream spec dependency not attested."),
            tool=tool_name,
            recommended_action="reduce_scope",
            details=result.get("details") or result.get("dependencies"),
        )

    sandbox_err = explicit
    if sandbox_err in (ERROR_DIRECT_WRITE_BLOCKED, ERROR_LEASE_EXPIRED, ERROR_LEASE_CONFLICT):
        return _failure(
            sandbox_err,
            str(result.get("error") or "Sandbox blocked write."),
            tool=tool_name,
            recommended_action="retry_chunk",
        )

    if result.get("rejected") or result.get("rejection_reason"):
        return _failure(
            ERROR_TRUSTCHAIN_REJECTED,
            result.get("error")
            or result.get("rejection_title")
            or "TrustChain rejected unnotarized change.",
            tool=tool_name,
            details={"rejection_reason": result.get("rejection_reason")},
        )

    if result.get("use_tool") == "apatch_apply_session":
        return _failure(
            ERROR_MASS_APPLY_BLOCKED,
            result.get("error") or "Mass apply blocked; use apatch_apply_session.",
            tool=tool_name,
            recommended_action="reduce_scope",
            details={"candidate_count": result.get("candidate_count")},
        )

    reason = str(result.get("reason") or "")
    if reason == "change_budget_exceeded" or "budget" in reason.lower():
        return _failure(
            ERROR_BUDGET_EXCEEDED,
            result.get("error") or "Change budget exceeded.",
            tool=tool_name,
            details=result.get("change_budget"),
        )

    verify_tools = ("apatch_verify_run", "apatch_verify_status")
    if result.get("verify_rollback") or (
        tool_name in verify_tools and result.get("ok") is False
    ) or ("verify" in reason.lower() and result.get("ok") is False):
        return _failure(
            ERROR_VERIFY_FAILED,
            result.get("error") or "Verify step failed.",
            tool=tool_name,
            recommended_action=_verify_recommended_action(result),
            details=_verify_failure_details(result),
        )

    violations: List[Dict[str, Any]] = result.get("violations") or []
    if violations and "arch" in tool_name:
        return _failure(
            ERROR_ARCH_VIOLATION,
            f"{len(violations)} architecture rule violation(s).",
            tool=tool_name,
            details={"violations": violations[:5]},
        )

    if violations and "sandbox" in tool_name:
        first_reason = str((violations[0] or {}).get("reason") or "")
        sandbox_err = {
            ERROR_DIRECT_WRITE_BLOCKED: ERROR_DIRECT_WRITE_BLOCKED,
            ERROR_LEASE_EXPIRED: ERROR_LEASE_EXPIRED,
            ERROR_LEASE_CONFLICT: ERROR_LEASE_CONFLICT,
            "direct_write_blocked": ERROR_DIRECT_WRITE_BLOCKED,
            "lease_expired": ERROR_LEASE_EXPIRED,
            "lease_conflict": ERROR_LEASE_CONFLICT,
        }.get(first_reason, ERROR_DIRECT_WRITE_BLOCKED)
        return _failure(
            sandbox_err,
            result.get("error") or result.get("reason") or f"{len(violations)} sandbox violation(s).",
            tool=tool_name,
            recommended_action="retry_chunk",
            details={"violations": violations[:5]},
        )

    is_db_tool = tool_name.startswith(("apatch_db", "db_"))
    if violations and (is_db_tool or "semantic" in tool_name):
        err = ERROR_DB_RISK if is_db_tool else ERROR_VERIFY_FAILED
        return _failure(
            err,
            result.get("error") or result.get("reason") or f"{len(violations)} violation(s).",
            tool=tool_name,
            details={"violations": violations[:5]},
        )

    if result.get("aborted"):
        return _failure(
            ERROR_SESSION_ABORTED,
            "Apply session aborted.",
            tool=tool_name,
            recommended_action="retry_chunk",
        )

    if not result.get("trustchain_committed", True) and result.get("applied", 0) > 0:
        return _failure(
            ERROR_NOTARIZATION_FAILED,
            "Files changed but TrustChain block was not committed.",
            tool=tool_name,
            recommended_action="rollback",
        )

    chunk = result.get("chunk_result") or {}
    if chunk.get("verify_rollback") or chunk.get("failed", 0) > 0:
        return _failure(
            ERROR_APPLY_FAILED,
            result.get("error") or "Chunk apply failed.",
            tool=tool_name,
            details=chunk,
        )

    if result.get("failed", 0) > 0 or result.get("error"):
        return _failure(
            ERROR_APPLY_FAILED,
            str(result.get("error") or "Apply failed."),
            tool=tool_name,
        )

    return _failure(
        ERROR_UNKNOWN,
        str(result.get("error") or "Operation failed."),
        tool=tool_name,
    )
