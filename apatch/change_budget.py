"""Change budget estimation for apply (R49)."""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Set

BUDGET_PRESETS: Dict[str, Dict[str, int]] = {
    "small": {"max_files": 5, "max_insertions": 100, "max_deletions": 50},
    "medium": {"max_files": 20, "max_insertions": 500, "max_deletions": 200},
    "large": {"max_files": 100, "max_insertions": 5000, "max_deletions": 2000},
}


def resolve_budget(
    preset: Optional[str] = None,
    *,
    max_files: Optional[int] = None,
    max_insertions: Optional[int] = None,
    max_deletions: Optional[int] = None,
) -> Optional[Dict[str, int]]:
    if preset:
        base = dict(BUDGET_PRESETS.get(preset, BUDGET_PRESETS["medium"]))
    elif max_files is None and max_insertions is None and max_deletions is None:
        return None
    else:
        base = {
            "max_files": max_files or 10**9,
            "max_insertions": max_insertions or 10**9,
            "max_deletions": max_deletions or 10**9,
        }
    if max_files is not None:
        base["max_files"] = max_files
    if max_insertions is not None:
        base["max_insertions"] = max_insertions
    if max_deletions is not None:
        base["max_deletions"] = max_deletions
    return base


def estimate_from_candidates(
    candidates: List[Any],
    target_dir: str,
    *,
    replace_all: bool = False,
    min_confidence: Optional[float] = None,
    only_drifted: bool = False,
) -> Dict[str, Any]:
    from apatch.matcher import ASTMatcher
    from apatch.resolver import resolve_smart_path

    files: Set[str] = set()
    insertions = 0
    deletions = 0

    for cand in candidates:
        resolved = resolve_smart_path(target_dir, cand.target_file, cand.action_type)
        if not resolved:
            continue
        ra = replace_all or getattr(cand, "replace_all", False)
        matcher = ASTMatcher(resolved)
        result = matcher.evaluate(
            cand.old_content,
            cand.new_content,
            cand.action_type,
            replace_all=ra,
        )
        if not result.success:
            continue
        if min_confidence is not None and result.confidence < min_confidence:
            continue
        if only_drifted and result.strategy in ("exact", "exact-all"):
            continue

        files.add(resolved)
        if cand.action_type == "CHMOD":
            continue
        old_lines = (matcher.content or "").splitlines()
        new_lines = (result.content or "").splitlines()
        for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
            if line.startswith("+") and not line.startswith("+++"):
                insertions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

    return {
        "files": len(files),
        "insertions": insertions,
        "deletions": deletions,
        "file_paths": sorted(files),
    }


def check_budget(estimate: Dict[str, Any], budget: Dict[str, int]) -> Dict[str, Any]:
    violations: List[str] = []
    if estimate["files"] > budget["max_files"]:
        violations.append("max_files")
    if estimate["insertions"] > budget["max_insertions"]:
        violations.append("max_insertions")
    if estimate["deletions"] > budget["max_deletions"]:
        violations.append("max_deletions")
    return {
        "ok": len(violations) == 0,
        "estimate": estimate,
        "budget": budget,
        "violations": violations,
        "reason": "change_budget_exceeded" if violations else None,
    }
