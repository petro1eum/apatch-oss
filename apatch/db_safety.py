"""Migration safety static analysis (R46)."""

from __future__ import annotations
from pathlib import Path

import os
import re
from typing import Any, Dict, List, Optional

from apatch.db_profiles import get_db_profile, migration_globs_for
from apatch.git_util import changed_rel_under_globs, files_matching_globs, find_git_root

_HIGH_PATTERNS = [
    (re.compile(r"\bDROP\s+TABLE\b", re.I), "drop_table", "DROP TABLE may destroy data"),
    (re.compile(r"drop_table\s*\(", re.I), "drop_table", "drop_table() may destroy data"),
    (re.compile(r"\bDROP\s+COLUMN\b", re.I), "drop_column", "DROP COLUMN may destroy data"),
    (re.compile(r"drop_column\s*\(", re.I), "drop_column", "DROP COLUMN may destroy data"),
    (re.compile(r"\bTRUNCATE\b", re.I), "truncate", "TRUNCATE may destroy data"),
    (re.compile(r"ALTER\s+TYPE\b", re.I), "alter_type", "ALTER TYPE may break compatibility"),
    (re.compile(r"alter_column\s*\([^)]*type", re.I), "alter_column_type", "ALTER COLUMN TYPE may break data"),
]

_MEDIUM_PATTERNS = [
    (re.compile(r"ALTER\s+TABLE\b", re.I), "alter_table", "ALTER TABLE may lock table"),
    (re.compile(r"nullable\s*=\s*False", re.I), "not_null", "NOT NULL without backfill may fail"),
]


def run_db_safety(
    target_dir: str,
    *,
    profile: str,
    since: Optional[str] = "HEAD",
    migration_glob: Optional[List[str]] = None,
) -> Dict[str, Any]:
    get_db_profile(profile)
    root = find_git_root(target_dir) or os.path.abspath(target_dir)
    globs = migration_globs_for(profile, migration_glob)

    if since:
        rel_files = sorted(changed_rel_under_globs(root, globs, since=since))
        paths = [os.path.join(root, r) for r in rel_files]
    else:
        paths = files_matching_globs(root, globs)

    findings: List[Dict[str, Any]] = []
    for abs_path in paths:
        if not os.path.isfile(abs_path):
            continue
        try:
            lines = Path(abs_path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        for lineno, line in enumerate(lines, 1):
            for patterns, risk in ((_HIGH_PATTERNS, "high"), (_MEDIUM_PATTERNS, "medium")):
                for rx, ftype, reason in patterns:
                    if rx.search(line):
                        findings.append({
                            "type": ftype,
                            "file": rel,
                            "line": lineno,
                            "snippet": line.strip()[:200],
                            "reason": reason,
                            "risk": risk,
                            "hint": "Add data migration or multi-phase deploy",
                        })

    overall_risk = "low"
    if any(f["risk"] == "high" for f in findings):
        overall_risk = "high"
    elif findings:
        overall_risk = "medium"

    return {
        "safe": len(findings) == 0,
        "risk": overall_risk,
        "profile": profile,
        "findings": findings,
        "scanned_files": len(paths),
    }
