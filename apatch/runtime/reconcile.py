"""Governed session reconcile after MCP disconnect (SPEC-SESSION-RECOVERY-1)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from apatch.runtime.domain import derive_lifecycle
from apatch.runtime.hints import hint_apply_session, hint_attest, hint_session_end
from apatch.session_state import (
    PHASE_APPLY,
    PHASE_COMPLETE,
    PHASE_VERIFY,
    load_session_state,
    save_session_state,
)


@dataclass
class ReconcileResult:
    applied: bool = False
    changes: Dict[str, Any] = field(default_factory=dict)
    effective_lifecycle: Optional[str] = None
    next_action: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applied": self.applied,
            "changes": self.changes,
            "effective_lifecycle": self.effective_lifecycle,
            "next_action": self.next_action,
            "reason": self.reason,
        }


def _ledger_has_attestation(root: str, session_id: str) -> bool:
    from apatch.spec import _ledger_entries

    entries, active = _ledger_entries(root)
    if not active:
        return False
    for entry in entries or []:
        if entry.get("tool_id") != "apatch":
            continue
        payload = entry.get("payload") or {}
        if payload.get("action") != "attest":
            continue
        gid = payload.get("governed_session_id") or payload.get("session_id")
        if gid == session_id:
            return True
    return False


def _load_apply_session(root: str) -> Optional[Dict[str, Any]]:
    from apatch.apply_session import default_session_path, load_session

    path = default_session_path(root)
    data = load_session(path)
    return data if isinstance(data, dict) else None


def _apply_in_progress(data: Dict[str, Any]) -> bool:
    chunks = data.get("chunks") or []
    if not chunks:
        return False
    idx = int(data.get("chunk_index", 0))
    return idx < len(chunks)


def reconcile_governed_session(target_dir: str) -> ReconcileResult:
    """Reconcile runtime evidence and persist at most one actual state change."""
    root = os.path.abspath(target_dir)
    raw = load_session_state(root)
    session_id = raw.get("session_id")
    if not session_id or not raw.get("intent") or raw.get("ended_at"):
        return ReconcileResult(applied=False, reason="no_open_session")

    changes: Dict[str, Any] = {}
    next_action: Optional[str] = None

    if _ledger_has_attestation(root, str(session_id)):
        changes.update(attested=True, phase=PHASE_COMPLETE, failure=None)
        next_action = hint_session_end()
    elif raw.get("attested") and not raw.get("ended_at"):
        changes.setdefault("phase", PHASE_COMPLETE)
        changes["failure"] = None
        next_action = hint_session_end()

    from apatch.verify_jobs import latest_verify_job_for_session

    vjob = latest_verify_job_for_session(root, str(session_id))
    if vjob and not changes.get("attested"):
        vstate = vjob.get("state")
        if vstate == "passed":
            vr = vjob.get("verify_result") or {}
            if isinstance(vr, dict) and vr.get("ok"):
                changes.setdefault("phase", PHASE_COMPLETE)
                changes["failure"] = None
                if not next_action:
                    next_action = hint_attest()
        elif vstate == "running":
            changes.setdefault("phase", PHASE_VERIFY)
            changes["failure"] = None

    apply_data = _load_apply_session(root)
    if apply_data and apply_data.get("session_id") == session_id:
        if _apply_in_progress(apply_data):
            changes.setdefault("phase", PHASE_APPLY)
            changes["failure"] = None
            progress = apply_data.get("progress") or {}
            if progress.get("chunks_total") is not None:
                done = int(progress.get("chunks_done") or apply_data.get("chunk_index") or 0)
                total = int(progress["chunks_total"])
                changes["budget_remaining"] = max(0, total - done)
            next_action = hint_apply_session()
        elif not changes.get("attested"):
            phase = changes.get("phase") or raw.get("phase")
            if phase in (None, "idle", "plan", PHASE_APPLY):
                changes["phase"] = PHASE_VERIFY
                changes["failure"] = None

    failure = raw.get("failure") if isinstance(raw.get("failure"), dict) else None
    if failure and "not allowed in lifecycle" in str(failure.get("message") or ""):
        phase = changes.get("phase") or raw.get("phase")
        if phase in (PHASE_VERIFY, PHASE_COMPLETE):
            changes["failure"] = None

    merged = {**raw, **changes}
    eff_failure = merged.get("failure") if isinstance(merged.get("failure"), dict) else None
    lifecycle = derive_lifecycle(
        merged.get("phase", "idle"),
        attested=bool(merged.get("attested")),
        failure=eff_failure,
    )
    if not next_action and merged.get("attested") and not raw.get("ended_at"):
        next_action = hint_session_end()
    elif not next_action and merged.get("phase") == PHASE_VERIFY and not merged.get("attested"):
        from apatch.runtime.hints import hint_verify_run

        next_action = hint_verify_run()
    elif not next_action and merged.get("phase") == PHASE_COMPLETE and not merged.get("attested"):
        next_action = hint_attest()

    if next_action:
        merged["next_action"] = next_action

    actual_changes = {
        key: value
        for key, value in merged.items()
        if key not in ("revision", "updated_at") and raw.get(key) != value
    }
    if not actual_changes:
        return ReconcileResult(
            applied=False,
            effective_lifecycle=lifecycle,
            next_action=next_action,
            reason="already_consistent",
        )

    save_session_state(
        root,
        merged,
        force=True,
        expected_session_id=session_id,
        expected_revision=int(raw.get("revision") or 0),
    )

    return ReconcileResult(
        applied=True,
        changes=actual_changes,
        effective_lifecycle=lifecycle,
        next_action=next_action,
    )
