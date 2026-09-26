"""Exact Git-index cleanup for four tracked APatch runtime files.

This operation never removes working-tree files and never stages source code.
"""

from __future__ import annotations

import os
import stat
import subprocess
from typing import Any, Dict, List, Sequence

from apatch.git_util import find_git_root

RUNTIME_PATHS = (
    ".apatch/events.jsonl",
    ".apatch/notarized_index.json",
    ".apatch/registry/artifacts.jsonl",
    ".apatch/registry/provenance.jsonl",
)


def _git(root: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        check=False,
        timeout=120,
    )


def _paths(data: bytes) -> List[str]:
    return sorted(
        part.decode("utf-8", errors="surrogateescape")
        for part in data.split(b"\0") if part
    )


def _fail(code: str, message: str, **details: Any) -> Dict[str, Any]:
    return {
        "ok": False,
        "error_type": code,
        "error": message,
        "recoverable": True,
        "recommended_action": "inspect_exact_runtime_git_scope",
        **details,
    }


def _preflight(root: str) -> Dict[str, Any]:
    if find_git_root(root) != root:
        return _fail("GIT_ROOT_REQUIRED", "Target must be the exact Git checkout root.")
    staged = _git(root, "diff", "--cached", "--name-only", "-z")
    if staged.returncode != 0:
        return _fail("GIT_COMMAND_FAILED", "Git index could not be inspected.")
    if staged.stdout:
        return _fail("GIT_INDEX_NOT_CLEAN", "Git index already contains staged files.")

    tracked = _git(root, "ls-files", "--cached", "-z", "--", *RUNTIME_PATHS)
    if tracked.returncode != 0 or _paths(tracked.stdout) != sorted(RUNTIME_PATHS):
        return _fail(
            "RUNTIME_TRACKING_SCOPE_MISMATCH",
            "Exactly the four declared runtime paths must still be tracked.",
            tracked=_paths(tracked.stdout),
        )
    for rel in RUNTIME_PATHS:
        path = os.path.join(root, rel)
        try:
            mode = os.stat(path, follow_symlinks=False).st_mode
        except OSError:
            return _fail("RUNTIME_FILE_MISSING", "Runtime file is missing.", path=rel)
        if not stat.S_ISREG(mode) or os.path.realpath(path) != path:
            return _fail("RUNTIME_PATH_UNSAFE", "Runtime path is not a regular file.", path=rel)
        ignored = _git(root, "check-ignore", "--no-index", "-q", "--", rel)
        if ignored.returncode != 0:
            return _fail(
                "RUNTIME_IGNORE_MISSING",
                "Runtime path must already be ignored before untracking.",
                path=rel,
            )
    return {"ok": True, "paths": list(RUNTIME_PATHS)}


def untrack_runtime_files_workspace(
    target_dir: str = ".",
    *,
    message: str = "Stop tracking APatch runtime files",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Remove only allowlisted runtime files from Git index and commit deletion."""
    root = os.path.realpath(os.path.abspath(target_dir))
    checked = _preflight(root)
    if not checked.get("ok"):
        return checked
    if dry_run:
        return {
            "ok": True, "dry_run": True, "paths": list(RUNTIME_PATHS),
            "working_tree_files_removed": False,
        }
    if not str(message or "").strip():
        return _fail("GIT_COMMIT_MESSAGE_REQUIRED", "Commit message is required.")

    from apatch.runtime.runtime import MutationRuntime
    from apatch.runtime.state_machine import OP_APPLY_SESSION, assert_operation
    from apatch.trustchain_helper import TrustChainHelper

    rt = MutationRuntime(root)
    opened = rt.open_session(
        "remove four ignored APatch runtime paths from Git index",
        artifacts=["ticket:SEARCH-RUNTIME-UNTRACK-20260923"],
    )
    if not opened.get("ok"):
        return opened

    staged_by_us = False
    try:
        rt._ensure_mutation("git_untrack_runtime")
        assert_operation(root, OP_APPLY_SESSION)
        rt._capture_binding("git_untrack_runtime")
        checked = _preflight(root)
        if not checked.get("ok"):
            return checked

        removed = _git(root, "rm", "--cached", "-q", "--", *RUNTIME_PATHS)
        if removed.returncode != 0:
            return _fail("GIT_UNTRACK_FAILED", "Git could not untrack the exact runtime paths.")
        staged_by_us = True

        tc = TrustChainHelper(root, auto_init=True)
        if not tc.commit_action(
            "apatch",
            {
                "action": "git_untrack_runtime",
                "paths": list(RUNTIME_PATHS),
                "working_tree_files_removed": False,
            },
        ):
            return _fail("RUNTIME_UNTRACK_NOTARIZATION_FAILED", "No signed Git hygiene receipt.")
        mutation = rt._finish(
            "apatch_git_untrack_runtime",
            {
                "ok": True,
                "paths": list(RUNTIME_PATHS),
                "working_tree_files_removed": False,
                "trustchain_committed": True,
            },
        )
        if not mutation.get("ok"):
            return mutation

        staged = _git(root, "diff", "--cached", "--name-only", "-z")
        deletions = _git(root, "diff", "--cached", "--name-only", "--diff-filter=D", "-z")
        intact = all(os.path.isfile(os.path.join(root, rel)) for rel in RUNTIME_PATHS)
        valid = (
            staged.returncode == 0
            and deletions.returncode == 0
            and _paths(staged.stdout) == sorted(RUNTIME_PATHS)
            and _paths(deletions.stdout) == sorted(RUNTIME_PATHS)
            and intact
        )
        verified = rt._finish(
            "apatch_verify_run",
            {
                "ok": valid,
                "verify_kind": "exact_runtime_index_deletion",
                "staged_paths": _paths(staged.stdout),
                "working_tree_files_removed": not intact,
            },
        )
        if not verified.get("ok"):
            return _fail("RUNTIME_UNTRACK_VERIFY_FAILED", "Git index or working files differ from exact scope.")

        attested = rt.attest(message="Only four ignored runtime paths were removed from Git tracking")
        if not attested.get("ok"):
            return attested

        committed = _git(root, "commit", "-m", str(message).strip())
        if committed.returncode != 0:
            return _fail("GIT_COMMIT_FAILED", "Git rejected the exact runtime-only commit.")
        staged_by_us = False
        head = _git(root, "rev-parse", "HEAD")
        return {
            "ok": True,
            "committed": True,
            "commit": head.stdout.decode("ascii", errors="replace").strip(),
            "paths": list(RUNTIME_PATHS),
            "working_tree_files_removed": False,
            "session_id": rt.session_id,
        }
    finally:
        if staged_by_us:
            staged = _git(root, "diff", "--cached", "--name-only", "-z")
            if staged.returncode == 0 and _paths(staged.stdout) == sorted(RUNTIME_PATHS):
                _git(root, "reset", "-q", "HEAD", "--", *RUNTIME_PATHS)
        rt.close_session()
