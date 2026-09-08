"""Session/blocker projection for project status (SPEC-STATUS-BLOCKED-1)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

MAX_BASELINE_NODES = 20
MAX_SUMMARY_LEN = 240


def _cap_list(items: List[str], limit: int = MAX_BASELINE_NODES) -> Dict[str, Any]:
    if len(items) <= limit:
        return {"items": items, "truncated": False}
    return {"items": items[:limit], "truncated": True}


def build_blocker_summary(
    session_state: Dict[str, Any],
    root: str,
) -> Optional[Dict[str, Any]]:
    """Supervisor-safe blocker from persisted session failure (no pytest log)."""
    from apatch.session_state import PHASE_BLOCKED

    failure = session_state.get("failure")
    phase = session_state.get("phase")
    if not isinstance(failure, dict) and phase != PHASE_BLOCKED:
        return None
    if not isinstance(failure, dict):
        failure = {
            "error_type": "UNKNOWN",
            "recommended_action": "rollback",
            "recoverable": True,
            "message": f"Session blocked in phase {phase}",
            "tool": session_state.get("last_tool"),
        }

    error_type = str(failure.get("error_type") or "UNKNOWN")
    action = str(failure.get("recommended_action") or "rollback")
    summary = format_blocker_summary(failure, root)
    if action == "fix_forward" and "rollback" in summary.lower():
        summary = summary.replace("rollback", "fix forward", 1)

    return {
        "error_type": error_type,
        "recommended_action": action,
        "recoverable": bool(failure.get("recoverable", True)),
        "summary": summary[:MAX_SUMMARY_LEN],
        "tool": failure.get("tool") or session_state.get("last_tool"),
        "details_ref": None,
    }


def format_blocker_summary(failure: Dict[str, Any], root: str) -> str:
    """One-line human summary; uses baseline file when present."""
    from apatch.verify_baseline import load_baseline

    msg = str(failure.get("message") or failure.get("error_type") or "Operation failed")
    if len(msg) > MAX_SUMMARY_LEN:
        msg = msg[: MAX_SUMMARY_LEN - 3] + "..."

    base = load_baseline(root) or {}
    failures = base.get("failures") or []
    if failures and "verify" in str(failure.get("tool") or "").lower():
        sample = failures[0]
        extra = len(failures) - 1
        tail = f" (+{extra} more)" if extra > 0 else ""
        return f"Verify failed: {len(failures)} baseline failure(s) e.g. {sample}{tail}"

    error_type = failure.get("error_type") or "UNKNOWN"
    action = failure.get("recommended_action") or "inspect"
    return f"{error_type} → {action}: {msg.splitlines()[0][:120]}"


def build_baseline_summary(root: str) -> Optional[Dict[str, Any]]:
    from apatch.verify_baseline import load_baseline

    data = load_baseline(root)
    if not data:
        return None
    failures = list(data.get("failures") or [])
    capped = _cap_list(failures)
    return {
        "mode": "capture",
        "new_failures": [],
        "pre_existing_failures": capped["items"],
        "allowed_matched": [],
        "truncated": capped["truncated"],
        "verify_command": data.get("verify_command"),
    }


def build_session_block(root: str) -> Optional[Dict[str, Any]]:
    """Full session projection for project_status DTO."""
    from apatch.runtime.domain import derive_lifecycle
    from apatch.sandbox import sandbox_status_workspace
    from apatch.session_state import load_session_state

    raw = load_session_state(root)
    if not raw.get("session_id") or raw.get("ended_at"):
        return None

    failure = raw.get("failure") if isinstance(raw.get("failure"), dict) else None
    lifecycle = derive_lifecycle(
        raw.get("phase", "idle"),
        attested=bool(raw.get("attested")),
        failure=failure,
        ended=False,
    )
    lease = sandbox_status_workspace(root).get("lease") or {}
    block: Dict[str, Any] = {
        "session_id": raw.get("session_id"),
        "intent": raw.get("intent"),
        "phase": raw.get("phase"),
        "lifecycle": lifecycle,
        "checkpoint": raw.get("checkpoint"),
        "attested": bool(raw.get("attested")),
        "ended_at": raw.get("ended_at"),
        "lease_active": bool(lease.get("active")),
    }
    blocker = build_blocker_summary(raw, root)
    if blocker:
        block["blocker"] = blocker
    baseline = build_baseline_summary(root)
    if baseline:
        block["baseline"] = baseline
    return block


def active_session_alias(session: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Backward-compatible thin active_session from session block."""
    if not session:
        return None
    out: Dict[str, Any] = {
        "session_id": session.get("checkpoint") or session.get("session_id"),
        "phase": session.get("phase"),
    }
    if session.get("lease_active"):
        out["lease"] = {"active": True}
    return out