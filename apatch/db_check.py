"""DB model vs migration consistency check (R41)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from apatch.db_profiles import get_db_profile, migration_globs_for, model_globs_for
from apatch.git_util import changed_rel_under_globs, find_git_root


def run_db_check(
    target_dir: str,
    *,
    profile: str,
    since: str = "HEAD",
    model_glob: Optional[List[str]] = None,
    migration_glob: Optional[List[str]] = None,
) -> Dict[str, Any]:
    pack = get_db_profile(profile)
    model_globs = model_globs_for(profile, model_glob)
    mig_globs = migration_globs_for(profile, migration_glob)
    exclude = pack.get("migration_exclude_globs") or []

    root = find_git_root(target_dir) or os.path.abspath(target_dir)
    changed_models = sorted(changed_rel_under_globs(root, model_globs, since=since))
    changed_migs = sorted(changed_rel_under_globs(root, mig_globs, since=since))
    changed_migs = [m for m in changed_migs if not any(_rel_match(m, ex) for ex in exclude)]

    models_changed = len(changed_models) > 0
    if not models_changed:
        return {
            "ok": True,
            "profile": profile,
            "models_changed": False,
            "changed_model_files": [],
            "migrations_touched": list(changed_migs),
            "reason": None,
            "hint": None,
            "stack_check": None,
        }

    if not changed_migs:
        return {
            "ok": False,
            "profile": profile,
            "models_changed": True,
            "changed_model_files": changed_models,
            "migrations_touched": [],
            "reason": "missing_migration",
            "hint": f"Run: apatch db revision --profile {profile} --message \"...\"",
            "stack_check": None,
        }

    return {
        "ok": True,
        "profile": profile,
        "models_changed": True,
        "changed_model_files": changed_models,
        "migrations_touched": changed_migs,
        "reason": None,
        "hint": None,
        "stack_check": None,
    }


def _rel_match(rel: str, pattern: str) -> bool:
    from apatch.git_util import path_matches

    return path_matches(rel, pattern)
