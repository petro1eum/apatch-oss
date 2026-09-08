"""Cross-spec semantic cross-verify (RFP-014 Phase 2 / SPEC-INTERFERENCE-2).

Level-3 empirical check: backup workspace → apply source spec needles → run victim
spec ``(verify:)`` commands → rollback. Never leaves mutations on disk.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from apatch.backup import BackupManager
from apatch.spec import _ledger_entries, _load_spec
from apatch.spec_interference import (
    _needle_find_text,
    _needle_match_mode,
    _needle_replace_text,
    _needle_target_file,
    _simulate_replace,
    load_needles_for_spec,
)
from apatch.tool_paths import run_shell_verify


def apply_needles_sandboxed(
    target_dir: str,
    needles: List[Dict[str, Any]],
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply literal replace needles under a BackupManager session."""
    root = os.path.abspath(target_dir)
    sid = session_id or f"cross_verify_{int(time.time())}"
    mgr = BackupManager(root, session_id=sid)
    applied: List[str] = []
    failed: List[Dict[str, Any]] = []

    for step_index, needle in enumerate(needles):
        if _needle_match_mode(needle) != "literal":
            failed.append({"needle": needle, "error": "non_literal_match_mode"})
            continue
        rel = _needle_target_file(needle)
        if not rel:
            failed.append({"needle": needle, "error": "missing target_file"})
            continue
        abs_path = os.path.join(root, rel)
        find_text = _needle_find_text(needle)
        replace_text = _needle_replace_text(needle)
        if not find_text:
            failed.append({"needle": needle, "error": "missing find_text"})
            continue
        if not os.path.isfile(abs_path):
            failed.append({"needle": needle, "error": f"file not found: {rel}"})
            continue
        mgr.create_backup(abs_path, step_index=step_index)
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        if find_text not in content:
            failed.append({"needle": needle, "error": f"find_text not in {rel}"})
            continue
        new_content = _simulate_replace(
            content,
            find_text,
            replace_text,
            replace_all=bool(needle.get("replace_all")),
        )
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        applied.append(rel)

    return {
        "ok": not failed,
        "session_id": sid,
        "applied": applied,
        "failed": failed,
    }


def run_spec_verifies(
    target_dir: str,
    victim_spec_id: str,
    *,
    spec_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run each ``(verify:)`` command from a parsed spec."""
    root = os.path.abspath(target_dir)
    parsed = _load_spec(root, spec=victim_spec_id, spec_path=spec_path)
    results: List[Dict[str, Any]] = []
    for req in parsed.requirements:
        if not req.verify:
            results.append(
                {
                    "requirement": req.id,
                    "verify": None,
                    "ok": True,
                    "skipped": True,
                    "error": "",
                }
            )
            continue
        ok, err = run_shell_verify(req.verify, root)
        results.append(
            {
                "requirement": req.id,
                "verify": req.verify,
                "ok": ok,
                "skipped": False,
                "error": err,
            }
        )
    return results


def cross_verify_pair(
    target_dir: str,
    source_spec: str,
    victim_spec: str,
    needles: List[Dict[str, Any]],
    *,
    victim_spec_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply *source* needles, run *victim* verifies, always rollback."""
    root = os.path.abspath(target_dir)
    session_id = f"cross_verify_{source_spec}_{victim_spec}_{int(time.time())}"
    apply_result = apply_needles_sandboxed(root, needles, session_id=session_id)
    verify_results: List[Dict[str, Any]] = []
    semantic_conflicts: List[Dict[str, Any]] = []
    rolled_back = False
    rollback_error = ""

    try:
        if apply_result.get("applied"):
            verify_results = run_spec_verifies(
                root, victim_spec, spec_path=victim_spec_path
            )
            for vr in verify_results:
                if vr.get("skipped") or vr.get("ok"):
                    continue
                semantic_conflicts.append(
                    {
                        "type": "semantic_cross_verify",
                        "source_spec": source_spec,
                        "victim_spec": victim_spec,
                        "requirement": vr.get("requirement"),
                        "verify": vr.get("verify"),
                        "detail": vr.get("error") or "verify failed after source apply",
                        "severity": "high",
                    }
                )
    finally:
        try:
            restored = BackupManager.rollback_session(root, session_id)
            rolled_back = bool(restored)
        except Exception as exc:
            rollback_error = str(exc)

    all_passed = (
        apply_result.get("ok", False)
        and not semantic_conflicts
        and rolled_back
        and not rollback_error
    )
    return {
        "ok": True,
        "source_spec": source_spec,
        "victim_spec": victim_spec,
        "session_id": session_id,
        "apply": apply_result,
        "verify_results": verify_results,
        "semantic_conflicts": semantic_conflicts,
        "all_passed": all_passed,
        "rolled_back": rolled_back,
        "rollback_error": rollback_error,
    }


def spec_cross_verify_workspace(
    target_dir: str = ".",
    *,
    specs: Optional[List[str]] = None,
    include_planned: bool = True,
    include_attested: bool = False,
    pairwise: bool = True,
) -> Dict[str, Any]:
    """Run Level-3 cross-verify for ordered spec pairs."""
    root = os.path.abspath(target_dir)
    spec_ids = list(specs or [])
    if len(spec_ids) < 2:
        return {"ok": False, "error": "at least two spec ids required (pass specs=[...])"}

    for sid in spec_ids:
        try:
            _load_spec(root, spec=sid)
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "spec": sid}

    entries, ledger_active = _ledger_entries(root)
    checks: List[Dict[str, Any]] = []
    semantic_conflicts: List[Dict[str, Any]] = []
    pairs_tested = 0

    for source in spec_ids:
        victims = [v for v in spec_ids if v != source] if pairwise else spec_ids
        needles, data_sources = load_needles_for_spec(
            root,
            source,
            entries,
            include_attested=include_attested,
            include_planned=include_planned,
        )
        for victim in victims:
            if victim == source:
                continue
            pairs_tested += 1
            if not needles:
                checks.append(
                    {
                        "source_spec": source,
                        "victim_spec": victim,
                        "skipped": True,
                        "reason": "no needles for source spec",
                        "data_sources": data_sources,
                    }
                )
                continue
            pair = cross_verify_pair(root, source, victim, needles)
            pair["data_sources"] = data_sources
            checks.append(pair)
            semantic_conflicts.extend(pair.get("semantic_conflicts") or [])

    all_passed = pairs_tested > 0 and not semantic_conflicts and all(
        c.get("skipped") or c.get("all_passed") for c in checks
    )
    out: Dict[str, Any] = {
        "ok": True,
        "specs": spec_ids,
        "pairs_tested": pairs_tested,
        "checks": checks,
        "semantic_conflicts": semantic_conflicts,
        "all_passed": all_passed,
        "ledger_active": ledger_active,
        "analysis_level": 3,
    }
    if semantic_conflicts:
        out["error_type"] = "SPEC_CROSS_VERIFY_FAILED"
        out["recommended_action"] = "refactor_needles"
    return out


def spec_cross_verify_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root_dir = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_cross_verify",
        spec_cross_verify_workspace(root_dir, **kwargs),
        target_dir=root_dir,
    )