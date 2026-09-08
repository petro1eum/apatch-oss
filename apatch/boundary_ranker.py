"""Rank strip until-boundary candidates with heuristics + tree-sitter (R35)."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Sequence

from apatch.strip import _is_early_return_guard_line, suggest_until_candidates


def _heuristic_score(text: str, distance: int) -> float:
    score = max(0.2, 1.0 - distance / 120.0)
    stripped = text.strip()
    if stripped.startswith("//") and ("---" in stripped or "===" in stripped):
        score += 0.35
    if stripped.startswith("#") and ("---" in stripped or "===" in stripped):
        score += 0.35
    if "else if" in stripped:
        score += 0.25
    if stripped in ("}", "};"):
        score += 0.15
    if re.match(r"^\s*(export|function|class|const|let|def)\b", stripped):
        score += 0.2
    return min(score, 1.0)


def _lang_for_boundary_file(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    return {"tsx": "tsx", ".ts": "typescript", ".jsx": "jsx", ".js": "javascript"}.get(ext, "typescript")


def _ts_boundary_bonus(lines: Sequence[str], start_idx: int, cand_idx: int, *, file_path: str = "") -> float:
    try:
        from apatch.matcher import load_language
        from tree_sitter import Parser

        lang = load_language(_lang_for_boundary_file(file_path) if file_path else "typescript")
        if lang is None:
            return 0.0
        content = "".join(lines)
        parser = Parser(lang)
        tree = parser.parse(content.encode("utf-8"))
        cand_byte = sum(len(lines[i]) for i in range(cand_idx))
        bonus = 0.0
        for node in _walk(tree.root_node):
            if node.start_byte <= cand_byte <= node.end_byte:
                if node.type in (
                    "export_statement",
                    "function_declaration",
                    "method_definition",
                    "class_declaration",
                    "interface_declaration",
                    "lexical_declaration",
                ):
                    if abs(node.start_byte - cand_byte) < 8:
                        bonus = max(bonus, 0.4)
                if node.type == "comment" and node.start_byte == cand_byte:
                    bonus = max(bonus, 0.3)
        return bonus
    except Exception:
        return 0.0


def _py_boundary_bonus(lines: Sequence[str], start_idx: int, cand_idx: int) -> float:
    try:
        from apatch.matcher import load_language
        from tree_sitter import Parser

        lang = load_language("python")
        if lang is None:
            return 0.0
        content = "".join(lines)
        parser = Parser(lang)
        tree = parser.parse(content.encode("utf-8"))
        cand_byte = sum(len(lines[i]) for i in range(cand_idx))
        for node in _walk(tree.root_node):
            if node.start_byte <= cand_byte <= node.end_byte:
                if node.type in ("function_definition", "class_definition", "decorated_definition"):
                    if abs(node.start_byte - cand_byte) < 8:
                        return 0.35
        return 0.0
    except Exception:
        return 0.0


def _walk(node):
    yield node
    for i in range(node.child_count):
        yield from _walk(node.child(i))


def rank_until_candidates(
    file_path: str,
    lines: Sequence[str],
    start_idx: int,
) -> List[Dict[str, Any]]:
    """Return suggest-until candidates sorted by confidence (highest first)."""
    if file_path:
        from apatch.boundary_ast import rank_from_ast

        ast_ranked = rank_from_ast(file_path, lines, start_idx)
        if ast_ranked:
            return ast_ranked

    ext = os.path.splitext(file_path)[1].lower()
    ranked: List[Dict[str, Any]] = []
    seen_lines: set[int] = set()

    for line_no, text in suggest_until_candidates(lines, start_idx, file_path=file_path):
        cand_idx = line_no - 1
        if cand_idx in seen_lines:
            continue
        seen_lines.add(cand_idx)
        distance = line_no - (start_idx + 1)
        display = text.strip()
        kind = "default"
        if "[guard_return]" in display:
            kind = "guard_return"
            display = display.replace(" [guard_return]", "")
        elif "[root_return]" in display:
            kind = "root_return"
            display = display.replace(" [root_return]", "")
        elif "[jsx_anchor]" in display:
            kind = "jsx_anchor"
            display = display.replace(" [jsx_anchor]", "")
        elif _is_early_return_guard_line(lines, cand_idx):
            kind = "guard_return"
        elif re.search(r"\breturn\s*\(", display):
            kind = "root_return"
        score = _heuristic_score(display, distance)
        if kind == "guard_return":
            score = max(0.05, score - 0.45)
        elif kind == "jsx_anchor":
            score = min(1.0, score + 0.25)
        elif kind == "root_return":
            # Root JSX return is the canonical hook boundary regardless of distance.
            score = max(score, 0.88)
        if ext in (".ts", ".tsx", ".js", ".jsx"):
            score = min(1.0, score + _ts_boundary_bonus(lines, start_idx, cand_idx, file_path=file_path))
        elif ext == ".py":
            score = min(1.0, score + _py_boundary_bonus(lines, start_idx, cand_idx))
        ranked.append({
            "line": line_no,
            "text": display,
            "kind": kind,
            "score": round(score, 3),
            "confidence": round(score, 3),
            "distance": distance,
            "source": "heuristic",
        })

    ranked.sort(key=lambda x: (-x["score"], x["line"]))
    return ranked


_GENERIC_UNTIL = frozenset({"}", "};", ")", "})"})


def _is_generic_until_marker(until_marker: str) -> bool:
    stripped = (until_marker or "").strip()
    if stripped in _GENERIC_UNTIL:
        return True
    if re.fullmatch(r"^[};)\s]+$", stripped) and len(stripped) <= 6:
        return True
    return bool(re.fullmatch(r"^\s*\};\s*$", until_marker or ""))


def assess_until_marker(
    file_path: str,
    lines: Sequence[str],
    start_marker: str,
    until_marker: str,
) -> Dict[str, Any]:
    """Score manifest ``until`` / ``end_before`` against ranked candidates (R35)."""
    from apatch.strip import _find_line, _find_until_line

    try:
        start_idx = _find_line(lines, start_marker)
        end_idx = _find_until_line(lines, until_marker, start=start_idx + 1)
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "confidence": 0.0,
            "unstable": True,
            "error_type": "STRIP_BOUNDARY_UNSTABLE",
        }

    ranked = rank_until_candidates(file_path, lines, start_idx)
    matched = next((c for c in ranked if c["line"] - 1 == end_idx), None)
    until_text = lines[end_idx].rstrip() if end_idx < len(lines) else ""
    if matched:
        confidence = float(matched["confidence"])
        kind = matched["kind"]
    elif file_path:
        from apatch.boundary_ast import return_kind_at_line

        ast_kind = return_kind_at_line(file_path, lines, end_idx, start_idx=start_idx)
        if ast_kind is not None:
            kind = ast_kind
            confidence = 0.95 if ast_kind == "root_return" else 0.12
        elif re.search(r"\breturn\s*\(", until_text):
            is_guard = _is_early_return_guard_line(lines, end_idx, file_path=file_path, start_idx=start_idx)
            kind = "guard_return" if is_guard else "root_return"
            distance = end_idx - start_idx
            confidence = 0.55 if is_guard else min(0.92, 0.75 + max(0, 0.15 - distance / 400.0))
        else:
            confidence = 0.3
            kind = "manual"
    elif re.search(r"\breturn\s*\(", until_text):
        is_guard = _is_early_return_guard_line(lines, end_idx)
        kind = "guard_return" if is_guard else "root_return"
        distance = end_idx - start_idx
        confidence = 0.55 if is_guard else min(0.92, 0.75 + max(0, 0.15 - distance / 400.0))
    else:
        confidence = 0.3
        kind = "manual"

    if _is_generic_until_marker(until_marker):
        confidence = min(confidence, 0.32)

    unstable = confidence < 0.45 or (
        _is_generic_until_marker(until_marker) and (matched is None or kind == "default")
    )
    if kind == "root_return" and confidence >= 0.45:
        unstable = False
    recommended = ranked[0] if ranked else None

    boundary_source = "ast" if ranked and ranked[0].get("source") == "ast" else "heuristic"
    out: Dict[str, Any] = {
        "ok": True,
        "until_line": end_idx + 1,
        "until_text": lines[end_idx].rstrip() if end_idx < len(lines) else "",
        "confidence": round(confidence, 3),
        "kind": kind,
        "unstable": unstable,
        "boundary_source": boundary_source,
        "recommended": recommended,
        "candidates": ranked[:5],
    }
    if unstable:
        out["error_type"] = "STRIP_BOUNDARY_UNSTABLE"
        if recommended:
            out["recommended_until"] = recommended.get("text")
            out["recommended_line"] = recommended.get("line")
    return out
