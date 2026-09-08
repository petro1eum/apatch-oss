"""Resolve forward scan limits for strip until suggestions (AST-first, no fixed windows)."""

from __future__ import annotations

from typing import Sequence


def resolve_forward_scan_end(
    lines: Sequence[str],
    start_idx: int,
    *,
    file_path: str = "",
) -> int:
    """Exclusive end line for scanning forward from start_idx (function body or EOF)."""
    if start_idx < 0:
        return len(lines)

    if file_path:
        from apatch.boundary_ast import body_end_line_ast

        ast_end = body_end_line_ast(file_path, lines, start_idx)
        if ast_end is not None and ast_end > start_idx:
            return ast_end

    return _resolve_forward_scan_end_heuristic(lines, start_idx)


def _resolve_forward_scan_end_heuristic(lines: Sequence[str], start_idx: int) -> int:
    """Brace-balance fallback when tree-sitter grammar is unavailable."""
    import re

    open_idx = _find_block_open_line(lines, start_idx)
    if open_idx is not None:
        brace_end = _block_close_exclusive_line(lines, open_idx)
        if brace_end is not None and brace_end > start_idx:
            return brace_end
    return len(lines)


def _line_byte_offsets(lines: Sequence[str]):
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def _byte_to_line(offsets, byte_pos: int) -> int:
    lo, hi = 0, len(offsets) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if offsets[mid] <= byte_pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _find_block_open_line(lines: Sequence[str], start_idx: int):
    import re

    for i in range(start_idx, max(-1, start_idx - 120), -1):
        line = lines[i]
        if re.search(r"=>\s*\{", line) or re.search(r"\)\s*\{", line):
            return i
        if re.search(r"\bfunction\s+\w", line) and "{" in line:
            return i
    start_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    for i in range(start_idx, max(-1, start_idx - 120), -1):
        if "{" not in lines[i]:
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent < start_indent:
            return i
    return None


def _block_close_exclusive_line(lines: Sequence[str], open_line_idx: int):
    full = "".join(lines)
    offsets = _line_byte_offsets(lines)
    open_line = lines[open_line_idx]
    rel = open_line.find("{")
    if rel < 0:
        return None
    start = offsets[open_line_idx] + rel
    depth = 0
    for i in range(start, len(full)):
        ch = full[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                close_line = _byte_to_line(offsets, i)
                return min(close_line + 1, len(lines))
    return None
