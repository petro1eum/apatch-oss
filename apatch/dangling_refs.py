"""Detect identifiers removed by strip that parent file still references."""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple


_DEF_PATTERNS = [
    re.compile(r"\b(?:const|let|var|function)\s+([a-zA-Z_$][\w$]*)"),
    re.compile(r"\bfunction\s+([a-zA-Z_$][\w$]*)\s*\("),
    re.compile(r"\bclass\s+([a-zA-Z_$][\w$]*)"),
    re.compile(r"\b(?:async\s+)?([a-zA-Z_$][\w$]*)\s*=\s*(?:async\s*)?\("),
]


def extract_defined_names(content: str) -> Set[str]:
    names: Set[str] = set()
    for pat in _DEF_PATTERNS:
        names.update(pat.findall(content))
    return names


def extract_referenced_names(content: str) -> Set[str]:
  # Simple identifier scan; skip keywords
    keywords = {
        "if", "else", "for", "while", "return", "const", "let", "var", "function",
        "class", "import", "export", "from", "true", "false", "null", "undefined",
        "new", "this", "typeof", "void", "async", "await", "switch", "case", "break",
        "continue", "default", "try", "catch", "finally", "throw", "interface", "type",
    }
    tokens = re.findall(r"\b([a-zA-Z_$][\w$]*)\b", content)
    return {t for t in tokens if t not in keywords}


def find_dangling_references(
    removed_content: str,
    parent_remaining: str,
) -> List[Dict[str, object]]:
    """Return list of {name, lines} for symbols defined in removed block still used in parent."""
    defined = extract_defined_names(removed_content)
    if not defined:
        return []

    dangling: List[Dict[str, object]] = []
    parent_lines = parent_remaining.splitlines()
    for name in sorted(defined):
        line_nums: List[int] = []
        pat = re.compile(rf"\b{re.escape(name)}\b")
        for i, line in enumerate(parent_lines, start=1):
            if pat.search(line):
                line_nums.append(i)
        if line_nums:
            dangling.append({"name": name, "lines": line_nums})
    return dangling


def merge_dangling_reports(blocks: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Flatten per-block dangling into top-level list."""
    out: List[Dict[str, object]] = []
    seen: Set[str] = set()
    for block in blocks:
        for entry in block.get("dangling_references", []):
            name = entry.get("name", "")
            if name and name not in seen:
                seen.add(name)
                out.append(entry)
    return out
