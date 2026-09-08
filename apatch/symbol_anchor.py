"""Symbol-granular attestation anchoring (SPEC-SYMBOL-ANCHOR-1 / RFP-032).

Anchor attestation drift to named code symbols (functions/classes) rather than
whole-file hashes, so a requirement goes stale only when its own symbol changes.
Built on the tree-sitter grammars apatch already loads (apatch.matcher).
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional

_EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".java": "java",
    ".rb": "ruby",
}

_DEF_NODE_TYPES = {
    "function_definition",
    "class_definition",
    "function_declaration",
    "method_definition",
    "class_declaration",
    "function_item",
    "struct_item",
    "enum_item",
    "trait_item",
    "type_declaration",
    "method_declaration",
}


def _lang_for_path(path: str) -> Optional[str]:
    return _EXT_LANG.get(os.path.splitext(path)[1].lower())


def extract_file_symbols(path: str) -> Dict[str, Dict[str, Any]]:
    """Top-level named symbols -> {start_line, end_line, hash} (R1).

    Parsed with the tree-sitter grammar apatch already loads for the file's
    language. Returns {} for unsupported languages or unparsable/unreadable
    files (the signal to fall back to file-level behaviour). Deterministic.
    """
    lang_name = _lang_for_path(path)
    if not lang_name:
        return {}
    from apatch.matcher import load_language

    lang = load_language(lang_name)
    if lang is None:
        return {}
    try:
        import tree_sitter

        with open(path, "rb") as fh:
            data = fh.read()
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(data)
    except Exception:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for node in tree.root_node.children:
        target = node
        if node.type == "decorated_definition":
            target = next(
                (c for c in node.children if c.type in _DEF_NODE_TYPES), node
            )
        if target.type not in _DEF_NODE_TYPES:
            continue
        name_node = target.child_by_field_name("name")
        if name_node is None:
            continue
        name = data[name_node.start_byte : name_node.end_byte].decode("utf-8", "replace")
        body = data[node.start_byte : node.end_byte]
        out[name] = {
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "hash": hashlib.sha256(body).hexdigest(),
        }
    return out


def symbols_for_line_range(path: str, start_line: int, end_line: int) -> List[str]:
    """Names of top-level symbols whose span overlaps [start_line, end_line] (R2)."""
    symbols = extract_file_symbols(path)
    hits = [
        name
        for name, span in symbols.items()
        if span["start_line"] <= end_line and span["end_line"] >= start_line
    ]
    return sorted(hits)


def symbol_drift(anchors: Dict[str, Dict[str, str]], root: str = ".") -> List[str]:
    """Recorded {rel_path: {symbol: hash}} -> sorted ['rel::symbol', ...] changed (R3).

    A symbol is drifted when its current extracted hash differs from the recorded
    hash, or the symbol no longer exists. Matching symbols are not reported.
    """
    drifted: List[str] = []
    base = os.path.abspath(root)
    for rel, symbol_hashes in (anchors or {}).items():
        current = extract_file_symbols(os.path.join(base, rel))
        for name, ref_hash in (symbol_hashes or {}).items():
            cur = current.get(name)
            if cur is None or cur.get("hash") != ref_hash:
                drifted.append("{}::{}".format(rel, name))
    return sorted(drifted)


def requirement_stale_with_symbols(
    file_hashes: Dict[str, str],
    symbol_anchors: Optional[Dict[str, Dict[str, str]]],
    root: str = ".",
) -> bool:
    """Backward-compatible staleness (R5).

    No symbol_anchors -> behaves exactly as file-hash ``compute_drift`` (any
    drifted file = stale). With symbol_anchors -> stale only when an anchored
    symbol drifted; a drifted file with no symbol anchor falls back to
    file-level (stale). Never makes a file-hash-only requirement falsely stale.
    """
    from apatch.spec_coverage import compute_drift

    file_drifted = compute_drift(file_hashes or {}, root)
    if not file_drifted:
        return False
    if not symbol_anchors:
        return True
    for rel in file_drifted:
        if rel not in symbol_anchors:
            return True
    return bool(symbol_drift(symbol_anchors, root))