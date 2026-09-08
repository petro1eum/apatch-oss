"""Parallel plan evaluation worker (ADR-001 SCALE-6)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from apatch.match_session import MatchSession


def evaluate_plan_entry(
    payload: Dict[str, Any],
    *,
    match_session: Optional[MatchSession] = None,
) -> Dict[str, Any]:
    """Evaluate a single plan row (picklable for ProcessPoolExecutor when session is None)."""
    import difflib

    from apatch.matcher import ASTMatcher

    entry = dict(payload["entry"])
    resolved = payload.get("resolved_path")
    if not resolved:
        return entry

    try:
        if match_session is not None:
            matcher = match_session.matcher_for(resolved)
        else:
            matcher = ASTMatcher(resolved)
        result = matcher.evaluate(
            payload["old_content"],
            payload["new_content"],
            payload["action_type"],
            replace_all=payload.get("replace_all", False),
        )
        entry["strategy"] = result.strategy
        entry["confidence"] = result.confidence
        entry["would_apply"] = result.success
        entry["warnings"] = list(result.warnings)
        if payload["action_type"] == "CHMOD":
            if payload.get("show_diff") and result.success:
                entry["diff"] = "\n".join(result.warnings) + "\n"
            return entry
        if payload.get("show_diff") and result.success:
            diff = difflib.unified_diff(
                matcher.content.splitlines(keepends=True),
                result.content.splitlines(keepends=True),
                fromfile=f"a/{payload.get('basename', 'file')}",
                tofile=f"b/{payload.get('basename', 'file')}",
                n=3,
            )
            entry["diff"] = "".join(diff)
    except Exception as exc:
        entry["strategy"] = "error"
        entry["warnings"] = [str(exc)]
    return entry


def _evaluate_plan_entry_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process-pool entry point (no shared session)."""
    return evaluate_plan_entry(payload)


def parallel_plan_evaluate(
    jobs: list[Dict[str, Any]],
    *,
    workers: int,
) -> list[Dict[str, Any]]:
    """Run plan evaluations in parallel; preserves input order."""
    def sequential() -> list[Dict[str, Any]]:
        session = MatchSession()
        return [evaluate_plan_entry(job, match_session=session) for job in jobs]

    if workers <= 1 or len(jobs) <= 1:
        return sequential()

    from concurrent.futures import ProcessPoolExecutor

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            return list(
                pool.map(
                    _evaluate_plan_entry_worker,
                    jobs,
                    chunksize=max(1, len(jobs) // workers),
                )
            )
    except (NotImplementedError, PermissionError, OSError):
        return sequential()
