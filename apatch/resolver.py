"""Smart, drift-tolerant resolution of logged file paths to on-disk targets.

Agent transcripts often record absolute paths from a different machine or a
virtual runtime. These helpers map such paths back onto the local target tree
using a cascade: exact -> relative -> prefix-stripping -> recursive filename
search, always constrained to stay inside ``target_dir`` (no path traversal).
"""
from __future__ import annotations

import os
from typing import Optional

from apatch.path_index import PathIndex, prune_walk_dirs


def is_safe_subpath(target_dir: str, resolved_path: str) -> bool:
    """True iff ``resolved_path`` is strictly inside ``target_dir``."""
    real_target = os.path.realpath(target_dir)
    real_resolved = os.path.realpath(resolved_path)
    return real_resolved.startswith(real_target + os.sep) or real_resolved == real_target


def _resolve_by_basename(
    target_dir: str,
    filename: str,
    *,
    index: Optional[PathIndex] = None,
) -> Optional[str]:
    if index is not None:
        for candidate in index.resolve_basename(filename):
            if is_safe_subpath(target_dir, candidate):
                return os.path.abspath(candidate)
        return None

    for root, dirs, files in os.walk(target_dir):
        prune_walk_dirs(dirs)
        if filename in files:
            candidate = os.path.abspath(os.path.join(root, filename))
            if is_safe_subpath(target_dir, candidate):
                return candidate
    return None


def resolve_smart_path(
    target_dir: str,
    logged_path: str,
    action_type: str = "REPLACE",
    *,
    index: Optional[PathIndex] = None,
) -> Optional[str]:
    """Resolve a path recorded in a transcript to a safe local file path.

    Returns an absolute path inside ``target_dir``, or None if it cannot be
    resolved safely.

    ``index`` — optional session path index (ADR-001); avoids repeated ``os.walk``
    on basename fallback when resolving many candidates.
    """
    if not logged_path:
        return None

    target_dir = os.path.abspath(target_dir)

    # Ignore hidden .apatch backup path if it somehow made it into a log
    if ".apatch/backups" in logged_path or ".apatch\\backups" in logged_path:
        return None

    # For CREATE actions, the file might not exist yet
    if action_type == "CREATE":
        if os.path.isabs(logged_path):
            if is_safe_subpath(target_dir, logged_path):
                return os.path.abspath(logged_path)
            # Try stripping path drift for absolute paths
            parts = logged_path.replace("\\", "/").split("/")
            for i in range(1, len(parts)):
                subpath = "/".join(parts[i:])
                candidate = os.path.join(target_dir, subpath)
                if is_safe_subpath(target_dir, candidate):
                    return os.path.abspath(candidate)
        candidate = os.path.abspath(os.path.join(target_dir, logged_path))
        if is_safe_subpath(target_dir, candidate):
            return candidate

    # 1. If it exists as is
    if os.path.exists(logged_path):
        candidate = os.path.abspath(logged_path)
        if is_safe_subpath(target_dir, candidate):
            return candidate

    # 2. Try relative to target_dir
    direct_relative = os.path.join(target_dir, logged_path)
    if os.path.exists(direct_relative):
        candidate = os.path.abspath(direct_relative)
        if is_safe_subpath(target_dir, candidate):
            return candidate

    # 3. Handle absolute path drift (strip prefix elements from left to right)
    parts = logged_path.replace("\\", "/").split("/")
    for i in range(1, len(parts)):
        subpath = "/".join(parts[i:])
        candidate = os.path.join(target_dir, subpath)
        if os.path.exists(candidate):
            candidate_abs = os.path.abspath(candidate)
            if is_safe_subpath(target_dir, candidate_abs):
                return candidate_abs

    # 4. Fallback search by filename (indexed or pruned walk)
    filename = os.path.basename(logged_path)
    return _resolve_by_basename(target_dir, filename, index=index)
