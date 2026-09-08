"""Dependency impact analysis (R45)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from apatch.git_util import path_matches
from apatch.import_graph import build_import_graph
from apatch.symbol_index import build_symbol_index, find_symbol_definition


def run_impact(
    target: str,
    target_dir: str = ".",
    *,
    kind: Optional[str] = None,
    depth: int = 1,
    include_tests: bool = True,
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    resolved_kind = kind or _infer_kind(target)
    graph = build_import_graph(root)
    symbol_index = build_symbol_index(root)

    defined_in: Optional[str] = None
    start_files: Set[str] = set()

    if resolved_kind == "file":
        rel = _normalize_file_target(target, root)
        start_files.add(rel)
    else:
        defined_in = find_symbol_definition(symbol_index, target)
        if defined_in:
            start_files.add(defined_in)
        for ref in symbol_index.files_referencing(target):
            start_files.add(ref)
        if not start_files:
            return {
                "target": target,
                "kind": "symbol",
                "defined_in": None,
                "affected_files": [],
                "affected_tests": [],
                "affected_services": [],
                "graph_depth": depth,
                "edges": [],
                "warning": "symbol not found in index",
            }

    affected: Set[str] = set()
    edges_out: List[Dict[str, str]] = []
    for start in start_files:
        affected |= graph.dependents(start, depth=depth)
        if resolved_kind == "symbol":
            affected |= symbol_index.files_referencing(target)
        dep = graph.dependents(start, depth=depth)
        for src, dst, via in graph.edges:
            if dst == start or src in dep or dst in dep:
                edges_out.append({"from": src, "to": dst, "via": via})

    affected_list = sorted(affected)
    tests = [p for p in affected_list if _is_test_path(p)] if include_tests else []
    services = [p for p in affected_list if _is_service_path(p)]

    if resolved_kind == "symbol" and defined_in and defined_in not in affected_list:
        affected_list = sorted(set(affected_list) | {defined_in})

    return {
        "target": target,
        "kind": resolved_kind,
        "defined_in": defined_in,
        "affected_files": affected_list,
        "affected_tests": tests,
        "affected_services": services,
        "graph_depth": depth,
        "edges": edges_out[:200],
    }


def _infer_kind(target: str) -> str:
    if "/" in target or target.endswith((".py", ".ts", ".tsx", ".js")):
        return "file"
    return "symbol"


def _normalize_file_target(target: str, root: str) -> str:
    if os.path.isabs(target):
        return os.path.relpath(target, root).replace("\\", "/")
    return target.replace("\\", "/")


def _is_test_path(rel: str) -> bool:
    low = rel.lower()
    return (
        "/tests/" in low
        or low.startswith("tests/")
        or low.endswith("_test.py")
        or ".test." in low
        or ".spec." in low
    )


def _is_service_path(rel: str) -> bool:
    return path_matches(rel, "**/services/**") or path_matches(rel, "**/repositories/**")
