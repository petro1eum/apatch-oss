"""Discover agent transcript logs (.jsonl) on the local machine.

The biggest UX barrier to apatch was forcing the user to know the path to a
transcript. This module locates them automatically across the common agent
runtimes (Cursor, Claude Code, Gemini) and the current working directory.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass
class TranscriptInfo:
    path: str
    source: str
    mtime: float
    size: int
    candidate_count: Optional[int] = None

    def as_dict(self) -> dict:
        import datetime

        return {
            "path": self.path,
            "source": self.source,
            "mtime": self.mtime,
            "modified": datetime.datetime.fromtimestamp(self.mtime).isoformat(timespec="seconds"),
            "size": self.size,
            "candidate_count": self.candidate_count,
        }


# Known transcript locations keyed by a human-readable source label. Each entry
# is a glob (recursive globs use **) evaluated under the user's home directory.
_KNOWN_GLOBS = {
    "cursor": "~/.cursor/projects/*/agent-transcripts/*.jsonl",
    "claude": "~/.claude/projects/**/*.jsonl",
    "gemini": "~/.gemini/**/*.jsonl",
}


def _expand(pattern: str) -> List[str]:
    pattern = os.path.expanduser(pattern)
    return glob.glob(pattern, recursive=True)


def discover_transcripts(
    extra_paths: Sequence[str] = (),
    include_cwd: bool = True,
) -> List[TranscriptInfo]:
    """Locate candidate transcript files, most-recently-modified first.

    ``extra_paths`` may contain either .jsonl files or directories (searched
    one level deep). ``include_cwd`` adds ``./*.jsonl``.
    """
    found: dict[str, TranscriptInfo] = {}

    def add(path: str, source: str) -> None:
        if not os.path.isfile(path):
            return
        real = os.path.realpath(path)
        if real in found:
            return
        try:
            st = os.stat(real)
        except OSError:
            return
        found[real] = TranscriptInfo(path=real, source=source, mtime=st.st_mtime, size=st.st_size)

    for source, pattern in _KNOWN_GLOBS.items():
        for path in _expand(pattern):
            add(path, source)

    if include_cwd:
        for path in glob.glob(os.path.join(os.getcwd(), "*.jsonl")):
            add(path, "cwd")

    for raw in extra_paths:
        expanded = os.path.expanduser(raw)
        if os.path.isdir(expanded):
            for path in glob.glob(os.path.join(expanded, "*.jsonl")):
                add(path, "custom")
        else:
            add(expanded, "custom")

    results = list(found.values())
    results.sort(key=lambda t: t.mtime, reverse=True)
    return results


def count_candidates(path: str) -> Optional[int]:
    """Parse a transcript and return how many patch candidates it yields."""
    from apatch.ingestor import LogIngestor

    try:
        return len(LogIngestor(path).parse())
    except Exception:
        return None
