"""RFP-016 Phase 2 — GC report CLI/MCP (SPEC-HYGIENE-2)."""

from __future__ import annotations

import os
from typing import Any, Dict

PHASE2_MODES = frozenset({"report"})


def run_gc(
    target_dir: str = ".",
    *,
    mode: str = "report",
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Advisory GC report. Phase 2: report/dry-run only — no filesystem deletes."""
    mode_norm = str(mode or "report").strip().lower()
    root = os.path.abspath(target_dir)


    if mode_norm == "reconcile":
        from apatch.artifact_governance import gc_report, register_inferred_artifacts

        # Reconcile is an action mode (like rotate/safe): it always persists the
        # registry write. Use `report` mode for a dry preview of inferred paths.
        # Previously this honoured the dry_run default=True, so MCP apatch_gc —
        # which does not pass dry_run — silently registered nothing (a footgun).
        result = register_inferred_artifacts(root, dry_run=False)
        report = gc_report(root)
        return {
            "ok": True,
            "mode": "reconcile",
            "dry_run": False,
            "workspace": root,
            "report": report,
            **result,
            "inferred_count": report.get("inferred_count", 0),
            "status": report.get("status"),
        }

    if mode_norm == "rotate":
        from apatch.artifact_governance import GCInvariantViolation, gc_rotate

        try:
            result = gc_rotate(root)
        except GCInvariantViolation as exc:
            return {
                "ok": False,
                "error": str(exc),
                "error_type": "GC_INVARIANT_VIOLATION",
                "recoverable": True,
                "recommended_action": "reduce_scope",
                "workspace": root,
                "mode": "rotate",
            }
        return {"ok": True, "mode": "rotate", "dry_run": False, "workspace": root, **result}

    if mode_norm == "safe":
        from apatch.artifact_governance import GCInvariantViolation, gc_safe

        try:
            result = gc_safe(root)
        except GCInvariantViolation as exc:
            return {
                "ok": False,
                "error": str(exc),
                "error_type": "GC_INVARIANT_VIOLATION",
                "recoverable": True,
                "recommended_action": "reduce_scope",
                "workspace": root,
                "mode": "safe",
            }
        return {"ok": True, "mode": "safe", "dry_run": False, "workspace": root, **result}

    if mode_norm not in PHASE2_MODES:
        return {
            "ok": False,
            "error": f"unknown gc mode {mode!r}; allowed: {sorted(PHASE2_MODES)}",
            "error_type": "RUNTIME_TRANSITION",
            "recoverable": True,
            "recommended_action": "reduce_scope",
            "workspace": root,
            "allowed_modes": sorted(PHASE2_MODES),
        }

    from apatch.artifact_governance import gc_report

    report = gc_report(root)
    return {
        "ok": True,
        "mode": "report",
        "dry_run": bool(dry_run),
        "workspace": root,
        "report": report,
        **report,
    }