"""Session-scoped path index for basename resolution (ADR-001 SCALE-1/2/3/8)."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from apatch.scale_config import resolve_path_backend

# Directory names skipped during filesystem walks (SCALE-3).
SKIP_DIR_NAMES: frozenset[str] = frozenset({
    ".git",
    ".apatch",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
})

# fd --exclude patterns (directory names).
_FD_EXCLUDES = [".git", "node_modules", ".venv", "venv", "__pycache__"]


def prune_walk_dirs(dirs: List[str]) -> None:
    """In-place prune of ``dirs`` for ``os.walk`` (do not descend into SKIP dirs)."""
    dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]


def iter_target_files(target_dir: str) -> Iterator[str]:
    """Yield absolute file paths under ``target_dir`` with pruned directory walk."""
    target_dir = os.path.abspath(target_dir)
    for root, dirs, files in os.walk(target_dir):
        prune_walk_dirs(dirs)
        for name in files:
            yield os.path.join(root, name)


def list_workspace_file_paths(target_dir: str) -> List[str]:
    """Absolute paths under workspace — git index when available, else pruned walk."""
    target_dir = os.path.abspath(target_dir)
    git_paths = _git_tracked_under(target_dir)
    if git_paths is not None:
        return git_paths
    return list(iter_target_files(target_dir))


_INDEX_CACHE: Dict[str, tuple[float, "PathIndex"]] = {}


def _index_cache_signature(target_dir: str) -> float:
    git_root = find_git_root(target_dir)
    if git_root:
        index_path = os.path.join(git_root, ".git", "index")
        if os.path.isfile(index_path):
            return os.path.getmtime(index_path)
    return os.path.getmtime(target_dir)


def clear_path_index_cache() -> None:
    """Test helper — drop cached PathIndex instances."""
    _INDEX_CACHE.clear()


def find_git_root(start: str) -> Optional[str]:
    """Return git repository root containing ``start``, or None."""
    path = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def _git_ls_files(git_root: str) -> Optional[List[str]]:
    """Return all repo-relative tracked paths, or None if git unavailable."""
    try:
        proc = subprocess.run(
            ["git", "-C", git_root, "ls-files", "-z"],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout
    if not raw:
        return []
    return [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]


def _basename_map_from_abs(paths: List[str]) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    for abs_path in sorted(paths):
        base = os.path.basename(abs_path)
        mapping.setdefault(base, []).append(abs_path)
    return mapping


def _git_tracked_under(target_dir: str) -> Optional[List[str]]:
    git_root = find_git_root(target_dir)
    if git_root is None:
        return None
    rel_paths = _git_ls_files(git_root)
    if rel_paths is None:
        return None
    target_dir = os.path.abspath(target_dir)
    git_root = os.path.abspath(git_root)
    abs_paths: List[str] = []
    for rel in rel_paths:
        abs_path = os.path.abspath(os.path.join(git_root, rel))
        if abs_path.startswith(target_dir + os.sep):
            if os.path.isfile(abs_path):
                abs_paths.append(abs_path)
    return abs_paths


def _fd_files(target_dir: str) -> Optional[List[str]]:
    import shutil

    fd_bin = shutil.which("fd")
    if not fd_bin:
        return None
    target_dir = os.path.abspath(target_dir)
    cmd = [fd_bin, ".", target_dir, "--type", "f", "--absolute-path"]
    for name in _FD_EXCLUDES:
        cmd.extend(["--exclude", name])
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):
        return None
    lines = proc.stdout.decode("utf-8", errors="replace").splitlines()
    abs_paths = [p for p in lines if p and os.path.isfile(p)]
    root_prefix = target_dir + os.sep
    return [p for p in abs_paths if p.startswith(root_prefix)]


def _watchman_files(target_dir: str) -> Optional[List[str]]:
    import shutil

    watchman = shutil.which("watchman")
    if not watchman:
        return None
    target_dir = os.path.abspath(target_dir)
    try:
        subprocess.run(
            [watchman, "watch-project", target_dir],
            capture_output=True,
            check=False,
            timeout=30,
        )
        query = json.dumps(["query", target_dir, "glob", "**/*", {"expression": ["type", "f"]}])
        proc = subprocess.run(
            [watchman, "-j", query],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return None
    files_map = data.get("files") or {}
    abs_paths: List[str] = []
    for rel in files_map:
        abs_path = os.path.abspath(os.path.join(target_dir, rel))
        if abs_path.startswith(target_dir + os.sep) and os.path.isfile(abs_path):
            abs_paths.append(abs_path)
    return abs_paths


def _index_from_paths(target_dir: str, abs_paths: List[str], source: str) -> "PathIndex":
    return PathIndex(
        target_dir=os.path.abspath(target_dir),
        basename_map=_basename_map_from_abs(abs_paths),
        source=source,
    )


@dataclass
class PathIndex:
    """Maps basename -> absolute paths inside a target tree (built once per session)."""

    target_dir: str
    basename_map: Dict[str, List[str]] = field(default_factory=dict)
    source: str = "walk"  # "git" | "walk" | "fd" | "watchman"

    @classmethod
    def _build_uncached(
        cls,
        target_dir: str,
        *,
        backend: Optional[str] = None,
    ) -> PathIndex:
        mode = resolve_path_backend(backend)

        if mode in ("auto", "git"):
            git_paths = _git_tracked_under(target_dir)
            if git_paths is not None:
                return _index_from_paths(target_dir, git_paths, "git")
            if mode == "git":
                return _index_from_paths(target_dir, list(iter_target_files(target_dir)), "walk")

        if mode == "fd":
            fd_paths = _fd_files(target_dir)
            if fd_paths is not None:
                return _index_from_paths(target_dir, fd_paths, "fd")
            return _index_from_paths(target_dir, list(iter_target_files(target_dir)), "walk")

        if mode == "watchman":
            wm_paths = _watchman_files(target_dir)
            if wm_paths is not None:
                return _index_from_paths(target_dir, wm_paths, "watchman")
            return _index_from_paths(target_dir, list(iter_target_files(target_dir)), "walk")

        if mode == "walk":
            return _index_from_paths(target_dir, list(iter_target_files(target_dir)), "walk")

        return _index_from_paths(target_dir, list(iter_target_files(target_dir)), "walk")

    @classmethod
    def build(
        cls,
        target_dir: str,
        *,
        backend: Optional[str] = None,
        use_cache: bool = True,
    ) -> PathIndex:
        target_dir = os.path.abspath(target_dir)
        if use_cache:
            sig = _index_cache_signature(target_dir)
            cached = _INDEX_CACHE.get(target_dir)
            if cached and cached[0] == sig:
                return cached[1]
        index = cls._build_uncached(target_dir, backend=backend)
        if use_cache:
            _INDEX_CACHE[target_dir] = (_index_cache_signature(target_dir), index)
        return index

    def resolve_basename(self, filename: str) -> List[str]:
        """All indexed absolute paths with the given basename (sorted, may be empty)."""
        if not filename:
            return []
        return list(self.basename_map.get(filename, []))

    def first_basename(self, filename: str) -> Optional[str]:
        """First indexed match for basename fallback (sorted for determinism)."""
        matches = self.resolve_basename(filename)
        return matches[0] if matches else None
