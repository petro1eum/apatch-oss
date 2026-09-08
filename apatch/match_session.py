"""Per-session ASTMatcher cache (ADR-001 SCALE-4)."""
from __future__ import annotations

import os
from typing import Dict

from apatch.matcher import ASTMatcher


class MatchSession:
    """Reuse loaded file content across multiple patch candidates in one CLI session."""

    def __init__(self) -> None:
        self._matchers: Dict[str, ASTMatcher] = {}

    def matcher_for(self, path: str) -> ASTMatcher:
        abs_path = os.path.abspath(path)
        cached = self._matchers.get(abs_path)
        if cached is not None:
            return cached
        matcher = ASTMatcher(abs_path)
        self._matchers[abs_path] = matcher
        return matcher

    def invalidate(self, path: str) -> None:
        self._matchers.pop(os.path.abspath(path), None)

    def clear(self) -> None:
        self._matchers.clear()
