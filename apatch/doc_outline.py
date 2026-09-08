"""Markdown outline mutations — shift numbered headings, insert sections."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

_ATX_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")
_OUTLINE_RE = re.compile(
    r"^(?P<prefix>§?\s*)?"
    r"(?P<num>\d+(?:\.\d+)*)"
    r"(?P<sep>[\.\s:\-\u2014\u2013\u2009]|$)"
    r"(?P<rest>.*)$"
)


def atx_level(line: str) -> Optional[int]:
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return None
    m = _ATX_RE.match(line)
    if not m:
        return None
    return len(m.group("hashes"))


def split_outline_title(title: str) -> Optional[Tuple[str, str, str, str]]:
    """Return (prefix, num, sep, rest) when title starts with an outline number."""
    t = title.strip()
    m = _OUTLINE_RE.match(t)
    if not m:
        return None
    return m.group("prefix") or "", m.group("num"), m.group("sep"), m.group("rest")


def shift_outline_num(num: str, delta: int) -> str:
    parts = num.split(".")
    parts[0] = str(int(parts[0]) + delta)
    return ".".join(parts)


def renumber_header_line(line: str, delta: int, levels: Sequence[int]) -> str:
    lvl = atx_level(line)
    if lvl is None or lvl not in levels:
        return line
    m = _ATX_RE.match(line)
    if not m:
        return line
    parsed = split_outline_title(m.group("title"))
    if not parsed:
        return line
    prefix, num, sep, rest = parsed
    new_title = f"{prefix}{shift_outline_num(num, delta)}{sep}{rest}".rstrip()
    return f"{m.group('hashes')} {new_title}"


def find_anchor_index(lines: List[str], anchor: str) -> int:
    needle = anchor.strip()
    if not needle:
        raise ValueError("anchor must be non-empty")
    for i, line in enumerate(lines):
        if needle in line and atx_level(line) is not None:
            return i
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise ValueError(f"anchor not found: {anchor!r}")


def _preserve_trailing_newline(original: str, updated: str) -> str:
    if original.endswith("\n") and not updated.endswith("\n"):
        return updated + "\n"
    return updated


def shift_outline_in_text(
    content: str,
    *,
    after: str,
    levels: Sequence[int],
    delta: int,
    include_anchor: bool = True,
) -> str:
    """Increment the first outline segment for headers at ``levels`` from ``after`` onward."""
    if not levels:
        raise ValueError("levels must be non-empty")
    if delta == 0:
        return content
    normalized = content.replace("\r\n", "\n")
    lines = normalized.split("\n")
    start = find_anchor_index(lines, after)
    if not include_anchor:
        start += 1
    for i in range(start, len(lines)):
        lines[i] = renumber_header_line(lines[i], delta, levels)
    return _preserve_trailing_newline(normalized, "\n".join(lines))


def insert_before_in_text(content: str, *, before: str, insert: str) -> str:
    normalized = content.replace("\r\n", "\n")
    lines = normalized.split("\n")
    idx = find_anchor_index(lines, before)
    insert_block = insert.replace("\r\n", "\n")
    if insert_block and not insert_block.endswith("\n"):
        insert_block = insert_block + "\n"
    insert_lines = insert_block.split("\n")
    if insert_lines and insert_lines[-1] == "":
        insert_lines = insert_lines[:-1]
    lines[idx:idx] = insert_lines
    return _preserve_trailing_newline(normalized, "\n".join(lines))


def insert_section_in_text(
    content: str,
    *,
    before: str,
    section_content: str,
    shift_following: Optional[dict] = None,
) -> str:
    """Insert a section and optionally shift numbered headings at/after the anchor."""
    normalized = content.replace("\r\n", "\n")
    lines = normalized.split("\n")
    idx = find_anchor_index(lines, before)
    if shift_following:
        levels = shift_following.get("levels") or [2, 3]
        delta = int(shift_following.get("delta", 1))
        for i in range(idx, len(lines)):
            lines[i] = renumber_header_line(lines[i], delta, levels)
    insert_block = section_content.replace("\r\n", "\n")
    if insert_block and not insert_block.endswith("\n"):
        insert_block = insert_block + "\n"
    insert_lines = insert_block.split("\n")
    if insert_lines and insert_lines[-1] == "":
        insert_lines = insert_lines[:-1]
    lines[idx:idx] = insert_lines
    return _preserve_trailing_newline(normalized, "\n".join(lines))