"""Contract edges on unified diagnostics (SPEC-CONTRACT-EDGES-1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from apatch.build_diagnose import _find_type_header, _short_type_name
from apatch.symbol_index import build_symbol_index, find_symbol_definition

_EMPTY_EDGES: Dict[str, List[str]] = {
    "symbols": [],
    "files": [],
    "requirements": [],
    "artifacts": [],
}


def _empty_edges() -> Dict[str, Any]:
    return {k: list(v) for k, v in _EMPTY_EDGES.items()}


def _merge_edge_lists(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("symbols", "files", "requirements", "artifacts"):
        combined = list(base.get(key) or []) + list(extra.get(key) or [])
        out[key] = sorted(set(combined))
    if extra.get("impact_ref"):
        out["impact_ref"] = extra["impact_ref"]
    elif base.get("impact_ref"):
        out["impact_ref"] = base["impact_ref"]
    return out


def _find_cpp_referencing_files(
    root: str,
    class_name: str,
    *,
    member: Optional[str] = None,
) -> List[str]:
    root_path = Path(root)
    hits: List[str] = []
    for pattern in ("*.cpp", "*.cc", "*.cxx"):
        for path in root_path.rglob(pattern):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not re.search(rf"\b{re.escape(class_name)}\b", text):
                continue
            if member and member not in text:
                continue
            try:
                rel = str(path.relative_to(root_path)).replace("\\", "/")
            except ValueError:
                rel = str(path)
            hits.append(rel)
    return sorted(set(hits))


def _load_knowledge_map(target_dir: str) -> Dict[str, Any]:
    root = Path(target_dir)
    for candidate in (root / "knowledge_map.json", root / ".apatch" / "knowledge_map.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                return data
    return {}


def _sid_from_symbol(symbol: str) -> Optional[str]:
    text = symbol.strip()
    if text.startswith("@sid:"):
        return text.split(":", 1)[1]
    if re.fullmatch(r"claim_\d+", text):
        return text
    return None


def _resolve_cpp_missing_member(
    edges: Dict[str, Any],
    symbol: str,
    target_dir: str,
) -> None:
    edges["symbols"].append(symbol)
    parts = symbol.split("::")
    member = parts[-1] if len(parts) >= 2 else None
    qualified_type = "::".join(parts[:-1]) if len(parts) >= 2 else ""
    class_name = _short_type_name(qualified_type) if qualified_type else _short_type_name(symbol)

    type_def = _find_type_header(target_dir, class_name)
    if type_def and type_def.get("file"):
        edges["files"].append(type_def["file"])

    for rel in _find_cpp_referencing_files(target_dir, class_name, member=member):
        edges["files"].append(rel)

    edges["symbols"] = sorted(set(edges["symbols"]))
    edges["files"] = sorted(set(edges["files"]))


def _resolve_python_symbol(edges: Dict[str, Any], symbol: str, target_dir: str) -> None:
    index = build_symbol_index(target_dir)
    defined_in = find_symbol_definition(index, symbol)
    if not defined_in:
        return
    edges["symbols"].append(symbol)
    edges["files"].append(defined_in)
    for ref in sorted(index.files_referencing(symbol)):
        edges["files"].append(ref)
    edges["symbols"] = sorted(set(edges["symbols"]))
    edges["files"] = sorted(set(edges["files"]))


def _resolve_sid_artifacts(edges: Dict[str, Any], symbol: str, target_dir: str) -> None:
    sid = _sid_from_symbol(symbol)
    if not sid:
        return
    kmap = _load_knowledge_map(target_dir)
    block = kmap.get(sid)
    if not isinstance(block, dict):
        return
    content_hash = block.get("hash") or block.get("content_hash")
    if content_hash:
        edges["artifacts"].append(f"{sid}@{content_hash}")
    else:
        edges["artifacts"].append(f"@sid:{sid}")
    edges["artifacts"] = sorted(set(edges["artifacts"]))


def _attach_impact_ref(edges: Dict[str, Any], target_dir: str) -> None:
    symbols = edges.get("symbols") or []
    if not symbols:
        return
    target = symbols[0]
    if "::" in target:
        target = target.split("::")[-1]
    index = build_symbol_index(target_dir)
    if not find_symbol_definition(index, target):
        return
    from apatch.impact import run_impact

    imp = run_impact(target, target_dir, kind="symbol", depth=1)
    affected = imp.get("affected_files") or []
    if not imp.get("defined_in") and not affected:
        return
    edges["impact_ref"] = {
        "target": target,
        "kind": "symbol",
        "affected_files_count": len(affected),
    }


def resolve_symbol_edges(diagnostic: Dict[str, Any], target_dir: str) -> Dict[str, Any]:
    """Resolve contract edges for a unified diagnostic; empty lists when no symbol."""
    location = diagnostic.get("location") or {}
    symbol = location.get("symbol")
    existing = dict(diagnostic.get("edges") or {})
    if not symbol:
        return _merge_edge_lists(_empty_edges(), existing)

    edges = _empty_edges()
    for key in ("requirements", "artifacts"):
        if existing.get(key):
            edges[key] = list(existing[key])

    diag_type = diagnostic.get("type")
    source_file = location.get("file")
    if diag_type == "test_failure" and source_file:
        # The pytest adapter already provides the exact node and file. Building
        # a repository symbol index per parametrized failure would make failure
        # reporting O(failures * repository_size).
        edges["symbols"].append(str(symbol))
        edges["files"].append(str(source_file))
        _resolve_sid_artifacts(edges, str(symbol), target_dir)
        return _merge_edge_lists(existing, edges)
    if diag_type == "missing_member":
        _resolve_cpp_missing_member(edges, str(symbol), target_dir)
    elif diag_type not in ("spec_violation",):
        simple = str(symbol).split("::")[-1]
        _resolve_python_symbol(edges, simple, target_dir)

    _resolve_sid_artifacts(edges, str(symbol), target_dir)
    _attach_impact_ref(edges, target_dir)
    return _merge_edge_lists(existing, edges)


def merge_edges_into_diagnostic(diagnostic: Dict[str, Any], target_dir: str) -> Dict[str, Any]:
    """Attach resolved edges to diagnostic in place."""
    merged = resolve_symbol_edges(diagnostic, target_dir)
    if merged:
        diagnostic["edges"] = merged
    return diagnostic
