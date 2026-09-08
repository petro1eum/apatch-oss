"""Git helpers for orchestration tools."""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Set

import fnmatch

from apatch.apatch_paths import normalize_rel


def find_git_root(start: str) -> Optional[str]:
    path = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def git_changed_files(workspace: str, since: str = "HEAD") -> List[str]:
    """Repo-relative paths changed between ``since`` and working tree (incl. untracked)."""
    root = find_git_root(workspace) or os.path.abspath(workspace)
    changed: Set[str] = set()
    try:
        proc = subprocess.run(
            ["git", "-C", root, "diff", "--name-only", since, "--"],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if proc.returncode == 0:
            text = proc.stdout.decode("utf-8", errors="replace")
            changed.update(ln.strip() for ln in text.splitlines() if ln.strip())
        proc2 = subprocess.run(
            ["git", "-C", root, "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if proc2.returncode == 0:
            text2 = proc2.stdout.decode("utf-8", errors="replace")
            changed.update(ln.strip() for ln in text2.splitlines() if ln.strip())
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted(changed)


def path_matches(rel_path: str, pattern: str) -> bool:
    """Match repository-relative paths without stripping control-plane dots."""
    from pathlib import PurePosixPath

    rel = normalize_rel(rel_path)
    pat = pattern.replace("\\", "/")
    if PurePosixPath(rel).match(pat):
        return True
    if pat.startswith("**/"):
        if PurePosixPath(rel).match(pat[3:]):
            return True
    return fnmatch.fnmatch(rel, pat)


def files_matching_globs(root: str, globs: List[str]) -> List[str]:
    """Absolute paths under root matching any glob (repo-relative patterns)."""
    root = os.path.abspath(root)
    hits: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {".git", "node_modules", ".venv", "venv", "__pycache__", ".apatch"}
        ]
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, root).replace("\\", "/")
            if any(path_matches(rel, g) for g in globs):
                hits.append(abs_path)
    return sorted(hits)


def filter_paths_by_globs(paths: List[str], root: str, globs: List[str]) -> List[str]:
    root = os.path.abspath(root)
    out: List[str] = []
    for p in paths:
        rel = os.path.relpath(p, root).replace("\\", "/") if os.path.isabs(p) else p.replace("\\", "/")
        if any(path_matches(rel, g) for g in globs):
            out.append(p)
    return out


def changed_under_globs(workspace: str, globs: List[str], since: str = "HEAD") -> List[str]:
    root = find_git_root(workspace) or os.path.abspath(workspace)
    rel_changed = git_changed_files(workspace, since=since)
    abs_paths = [os.path.join(root, r) for r in rel_changed]
    return filter_paths_by_globs(abs_paths, root, globs)


def changed_rel_under_globs(workspace: str, globs: List[str], since: str = "HEAD") -> Set[str]:
    root = find_git_root(workspace) or os.path.abspath(workspace)
    rel_changed = git_changed_files(workspace, since=since)
    return {r for r in rel_changed if any(path_matches(r, g) for g in globs)}
