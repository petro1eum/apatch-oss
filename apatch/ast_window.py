"""Windowed tree-sitter parse helpers (ADR-001 SCALE-5)."""
from __future__ import annotations

import re
from typing import Tuple


def find_parse_anchor(content: str, old_str: str) -> int:
    """Best-effort byte offset in ``content`` to center an AST parse window."""
    if not content:
        return 0

    needle = old_str.strip()
    if needle:
        idx = content.find(needle)
        if idx >= 0:
            return idx

        for line in needle.splitlines():
            line = line.strip()
            if len(line) >= 8:
                idx = content.find(line)
                if idx >= 0:
                    return idx

        pattern_parts = []
        for char in old_str[:512]:
            if char.isspace():
                if not pattern_parts or pattern_parts[-1] != r"\s*":
                    pattern_parts.append(r"\s*")
            else:
                pattern_parts.append(re.escape(char))
        if pattern_parts:
            match = re.search("".join(pattern_parts), content, re.DOTALL)
            if match:
                return match.start()

    return len(content) // 2


def extract_parse_slice(content: str, anchor: int, window_bytes: int) -> Tuple[str, int]:
    """Return (slice_text, byte_offset) of at most ``window_bytes`` around ``anchor``."""
    if window_bytes <= 0 or len(content) <= window_bytes:
        return content, 0

    half = window_bytes // 2
    start = max(0, anchor - half)
    end = min(len(content), start + window_bytes)
    if end - start < window_bytes and end == len(content):
        start = max(0, end - window_bytes)

    # Expand to line boundaries so tree-sitter sees whole lines.
    while start > 0 and content[start - 1] not in "\n":
        start -= 1
    while end < len(content) and content[end : end + 1] not in "\n":
        end += 1

    return content[start:end], start
