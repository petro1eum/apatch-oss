"""AST-first strip boundary analysis via tree-sitter (hook / component strips)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_BODY_NODE_TYPES = frozenset({
    "statement_block",
    "block",
    "block_statement",
    "function_body",
    "body",
    "body_statement",
    "compound_statement",
})

_FUNCTION_NODE_TYPES = frozenset({
    "arrow_function",
    "function_declaration",
    "function_expression",
    "method_definition",
    "function_definition",
    "function_item",
    "generator_function",
    "generator_function_declaration",
})

_GUARD_ANCESTOR_TYPES = frozenset({
    "if_statement",
    "else_clause",
    "for_statement",
    "for_in_statement",
    "for_of_statement",
    "while_statement",
    "do_statement",
    "switch_statement",
    "switch_case",
    "case_clause",
    "catch_clause",
    "conditional_expression",
    "except_clause",
    "elif_clause",
})

_SECTION_COMMENT_RE = re.compile(r"(===|---|block)", re.I)


@dataclass
class AstBoundaryCandidate:
    line: int
    text: str
    kind: str
    confidence: float
    source: str = "ast"


@dataclass
class FunctionBoundaryAnalysis:
    body_end_line: int
    candidates: List[AstBoundaryCandidate] = field(default_factory=list)
    source: str = "ast"


def _lang_for_path(file_path: str) -> Optional[str]:
    ext = Path(file_path).suffix.lower()
    return {
        ".tsx": "tsx",
        ".ts": "typescript",
        ".jsx": "jsx",
        ".js": "javascript",
        ".py": "python",
    }.get(ext)


def _line_byte_offsets(lines: Sequence[str]) -> List[int]:
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def _byte_to_line(offsets: List[int], byte_pos: int) -> int:
    lo, hi = 0, len(offsets) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if offsets[mid] <= byte_pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _anchor_byte(lines: Sequence[str], start_idx: int, offsets: List[int]) -> int:
    if start_idx < 0 or start_idx >= len(lines):
        return 0
    return offsets[start_idx] + max(0, len(lines[start_idx]) // 2)


def _walk(node):
    yield node
    for i in range(node.child_count):
        yield from _walk(node.child(i))


def _parse_tree(file_path: str, lines: Sequence[str]):
    lang_name = _lang_for_path(file_path)
    if not lang_name:
        return None, None, None

    try:
        from tree_sitter import Parser

        from apatch.matcher import load_language
    except ImportError:
        return None, None, None

    language = load_language(lang_name)
    if language is None:
        return None, None, None

    content = "".join(lines)
    offsets = _line_byte_offsets(lines)
    parser = Parser(language)
    tree = parser.parse(content.encode("utf-8"))
    return tree, content, offsets


def _function_body(function_node) -> Optional[Any]:
    for child in function_node.children:
        if child.type in _BODY_NODE_TYPES:
            return child
    return None


def find_enclosing_function(
    tree,
    lines: Sequence[str],
    offsets: List[int],
    start_idx: int,
) -> Optional[Tuple[Any, Any]]:
    """Return (function_node, body_node) for the smallest function containing start_idx."""
    anchor = _anchor_byte(lines, start_idx, offsets)
    best: Optional[Tuple[Any, Any, int]] = None

    for node in _walk(tree.root_node):
        if node.start_byte <= anchor <= node.end_byte and node.type in _FUNCTION_NODE_TYPES:
            body = _function_body(node)
            if body is None:
                continue
            span = body.end_byte - body.start_byte
            if best is None or span < best[2]:
                best = (node, body, span)

    if best is None:
        return None
    return best[0], best[1]


def _function_owner(return_node, component_function):
    node = return_node.parent
    innermost = None
    while node and node != component_function:
        if node.type in _FUNCTION_NODE_TYPES:
            innermost = node
        node = node.parent
    return innermost or component_function


def _is_guard_return(return_node, body_block, component_function) -> bool:
    node = return_node.parent
    while node and node != body_block:
        if node.type in _GUARD_ANCESTOR_TYPES:
            return True
        if node.type in _FUNCTION_NODE_TYPES and node != component_function:
            return False
        node = node.parent
    return False


def _line_text(lines: Sequence[str], line_idx: int) -> str:
    if line_idx < 0 or line_idx >= len(lines):
        return ""
    return lines[line_idx].rstrip()


def _confidence_for_kind(kind: str) -> float:
    return {
        "root_return": 0.95,
        "guard_return": 0.12,
        "else_if": 0.62,
        "section_comment": 0.72,
        "jsx_anchor": 0.55,
        "export_boundary": 0.58,
        "block_close": 0.18,
    }.get(kind, 0.35)


def _collect_return_candidates(
    body_node,
    component_function,
    lines: Sequence[str],
    offsets: List[int],
    content: str,
) -> List[AstBoundaryCandidate]:
    out: List[AstBoundaryCandidate] = []
    seen_lines: set[int] = set()

    for node in _walk(body_node):
        if node.type != "return_statement":
            continue
        if _function_owner(node, component_function) != component_function:
            continue
        line_idx = _byte_to_line(offsets, node.start_byte)
        line_no = line_idx + 1
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        text = _line_text(lines, line_idx)
        if not text:
            text = content[node.start_byte : node.end_byte].splitlines()[0].strip()
        kind = "guard_return" if _is_guard_return(node, body_node, component_function) else "root_return"
        out.append(
            AstBoundaryCandidate(
                line=line_no,
                text=text,
                kind=kind,
                confidence=_confidence_for_kind(kind),
            )
        )
    return out


def _collect_other_candidates(
    body_node,
    lines: Sequence[str],
    offsets: List[int],
    start_idx: int,
) -> List[AstBoundaryCandidate]:
    out: List[AstBoundaryCandidate] = []
    seen: set[int] = set()

    for node in _walk(body_node):
        line_idx = _byte_to_line(offsets, node.start_byte)
        if line_idx <= start_idx:
            continue
        line_no = line_idx + 1
        if line_no in seen:
            continue

        if node.type == "if_statement":
            text = _line_text(lines, line_idx)
            if "else if" in text:
                seen.add(line_no)
                out.append(
                    AstBoundaryCandidate(
                        line=line_no,
                        text=text.strip(),
                        kind="else_if",
                        confidence=_confidence_for_kind("else_if"),
                    )
                )
        elif node.type == "comment":
            text = _line_text(lines, line_idx)
            if text.lstrip().startswith("//") and _SECTION_COMMENT_RE.search(text):
                seen.add(line_no)
                out.append(
                    AstBoundaryCandidate(
                        line=line_no,
                        text=text.strip(),
                        kind="section_comment",
                        confidence=_confidence_for_kind("section_comment"),
                    )
                )
    return out


def analyze_function_boundaries(
    file_path: str,
    lines: Sequence[str],
    start_idx: int,
) -> Optional[FunctionBoundaryAnalysis]:
    """AST analysis for strip until candidates; None when grammar/parse unavailable."""
    if not file_path or start_idx < 0:
        return None

    parsed = _parse_tree(file_path, lines)
    if parsed[0] is None:
        return None
    tree, _content, offsets = parsed

    found = find_enclosing_function(tree, lines, offsets, start_idx)
    if found is None:
        return None
    component_function, body_node = found

    body_end_line = min(_byte_to_line(offsets, body_node.end_byte) + 1, len(lines))
    candidates: List[AstBoundaryCandidate] = []
    candidates.extend(
        _collect_return_candidates(body_node, component_function, lines, offsets, "".join(lines))
    )
    candidates.extend(_collect_other_candidates(body_node, lines, offsets, start_idx))

    # Stable ordering: root returns last in file first for hook strips
    candidates.sort(key=lambda c: (c.line, -c.confidence))
    return FunctionBoundaryAnalysis(body_end_line=body_end_line, candidates=candidates)


def return_kind_at_line(
    file_path: str,
    lines: Sequence[str],
    line_idx: int,
    *,
    start_idx: int = 0,
) -> Optional[str]:
    """Return AST kind for ``return_statement`` at *line_idx*, or None."""
    analysis = analyze_function_boundaries(file_path, lines, start_idx)
    if analysis is None:
        return None
    line_no = line_idx + 1
    for cand in analysis.candidates:
        if cand.line == line_no and cand.kind in ("guard_return", "root_return"):
            return cand.kind
    return None


def body_end_line_ast(file_path: str, lines: Sequence[str], start_idx: int) -> Optional[int]:
    analysis = analyze_function_boundaries(file_path, lines, start_idx)
    if analysis is None:
        return None
    return analysis.body_end_line


def suggest_until_from_ast(
    file_path: str,
    lines: Sequence[str],
    start_idx: int,
) -> List[Tuple[int, str]]:
    """(1-based line, display text with [kind]) from AST; empty when unavailable."""
    analysis = analyze_function_boundaries(file_path, lines, start_idx)
    if analysis is None:
        return []
    out: List[Tuple[int, str]] = []
    for cand in analysis.candidates:
        display = cand.text if cand.text else cand.kind
        out.append((cand.line, f"{display} [{cand.kind}]"))
    return out


def rank_from_ast(
    file_path: str,
    lines: Sequence[str],
    start_idx: int,
) -> List[Dict[str, Any]]:
    """Ranked candidate dicts compatible with boundary_ranker output."""
    analysis = analyze_function_boundaries(file_path, lines, start_idx)
    if analysis is None:
        return []

    ranked: List[Dict[str, Any]] = []
    for cand in analysis.candidates:
        distance = cand.line - (start_idx + 1)
        ranked.append({
            "line": cand.line,
            "text": cand.text,
            "kind": cand.kind,
            "score": round(cand.confidence, 3),
            "confidence": round(cand.confidence, 3),
            "distance": distance,
            "source": "ast",
        })

    ranked.sort(key=lambda x: (-x["score"], x["line"]))
    return ranked
