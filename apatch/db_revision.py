"""DB migration revision draft wrapper (R42)."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

from apatch.db_profiles import revision_command


def run_db_revision(
    target_dir: str,
    *,
    profile: str,
    message: str = "apatch revision",
    dry_run: bool = False,
    command_override: Optional[List[str]] = None,
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    cmd = command_override or revision_command(profile, message)
    cmd_display = " ".join(cmd)

    if dry_run:
        return {
            "ok": True,
            "profile": profile,
            "command_run": cmd_display,
            "created_files": [],
            "stdout": "",
            "stderr": "",
            "dry_run": True,
            "hint": "Review migration SQL; add data migration steps if needed.",
        }

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {
            "ok": False,
            "profile": profile,
            "command_run": cmd_display,
            "created_files": [],
            "stdout": "",
            "stderr": str(e),
            "hint": None,
        }

    created = _detect_created_files(root, profile)
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "profile": profile,
        "command_run": cmd_display,
        "created_files": created,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "hint": "Review migration SQL; add data migration steps if needed." if ok else None,
    }


def _detect_created_files(root: str, profile: str) -> List[str]:
    from apatch.db_profiles import migration_globs_for
    from apatch.git_util import changed_rel_under_globs

    rels = changed_rel_under_globs(root, migration_globs_for(profile), since="HEAD")
    return sorted(rels)
