"""Append-only domain event stream (.apatch/events.jsonl)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from apatch.runtime.domain import SCHEMA_VERSION

EVENTS_REL = ".apatch/events.jsonl"


def events_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), EVENTS_REL)


def emit_domain_event(
    root: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """Append one domain event; returns path or None on failure."""
    path = events_path(root)
    record: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "payload": payload or {},
    }
    if session_id:
        record["session_id"] = session_id
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        from apatch.artifact_governance import register_on_write

        register_on_write(
            root,
            EVENTS_REL,
            class_name="LEDGER",
            created_by_tool="apatch_events",
            reason="domain event stream",
            governed_session_id=session_id,
        )
        return path
    except OSError:
        return None


def build_events_tail_view(root: str, limit: int = 20) -> Dict[str, Any]:
    """Read-only tail of domain event stream for MCP/CLI."""
    events = tail_events(root, limit=limit)
    return {
        "ok": True,
        "workspace": os.path.abspath(root),
        "events_path": events_path(root),
        "limit": limit,
        "count": len(events),
        "events": events,
    }


def tail_events(root: str, limit: int = 20) -> list:
    path = events_path(root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def emit_for_tool_result(
    tool_name: str,
    result: Dict[str, Any],
    target_dir: str,
    state: Dict[str, Any],
    failure: Any = None,
) -> None:
    """Map tool outcomes to domain events (single emitter for E2)."""
    session_id = state.get("session_id")
    payload_base: Dict[str, Any] = {"tool": tool_name, "phase": state.get("phase")}

    if tool_name in (
        "apatch_plan",
        "apatch_plan_batch",
        "apatch_generate",
        "apatch_generate_batch",
    ) and result.get("ok") is not False:
        emit_domain_event(target_dir, "MutationPlanned", payload_base, session_id=session_id)
    elif tool_name in (
        "apatch_apply",
        "apatch_apply_session",
        "apatch_strip",
        "apatch_phase_run",
        "apatch_governed_phase_run",
    ) and result.get("ok"):
        emit_domain_event(
            target_dir,
            "MutationApplied",
            {**payload_base, "checkpoint": result.get("checkpoint")},
            session_id=session_id,
        )
        chunk = result.get("chunk_result") or {}
        if result.get("trustchain_committed") or state.get("trustchain") or chunk.get("applied"):
            emit_domain_event(target_dir, "AttestationCommitted", payload_base, session_id=session_id)
    elif tool_name in ("apatch_apply", "apatch_apply_session") and (failure or result.get("ok") is False):
        err = getattr(failure, "error_type", None) if failure else "APPLY_FAILED"
        emit_domain_event(
            target_dir,
            "MutationFailed",
            {**payload_base, "error_type": err},
            session_id=session_id,
        )
    elif tool_name in ("apatch_verify_semantic", "apatch_sandbox_ci_gate", "apatch_pipeline_run"):
        evt = "VerificationPassed" if result.get("ok") else "VerificationFailed"
        emit_domain_event(target_dir, evt, payload_base, session_id=session_id)
    elif tool_name == "apatch_attest" and result.get("ok") and result.get("committed"):
        emit_domain_event(
            target_dir,
            "AttestationCommitted",
            {**payload_base, "intent": result.get("intent")},
            session_id=session_id or result.get("session_id"),
        )
    elif tool_name == "apatch_rollback" and result.get("ok"):
        emit_domain_event(target_dir, "RollbackCompleted", payload_base, session_id=session_id)
    elif tool_name in ("apatch_verify_run", "apatch_verify_semantic") and result.get("ok") is not None:
        evt = "VerificationPassed" if result.get("ok") else "VerificationFailed"
        emit_domain_event(target_dir, evt, payload_base, session_id=session_id)
    elif tool_name == "apatch_attestation_export" and result.get("ok"):
        emit_domain_event(
            target_dir,
            "AttestationExported",
            {**payload_base, "path": result.get("path"), "event_count": result.get("event_count")},
            session_id=session_id,
        )
