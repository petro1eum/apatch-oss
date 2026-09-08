"""Native in-process cross-file reference graph (RFP-033) — no external indexer.

apatch already parses code; depending on an out-of-band ``scip-python`` that nobody installs
left the impact feature dormant. This builds the cross-file reference graph from the Python
AST, import-resolved, so ``scip_impact_workspace`` works on any repo out of the box. A real
``.scip`` (if present) still wins as the more precise / multi-language source.

Resolution is import-aware (not bare-name): ``from a import foo; foo()`` and ``import a;
a.foo()`` both resolve to ``a.py``'s ``foo``. It mirrors ``ScipModel``'s read interface so
``scip_impacted_requirements`` consumes it unchanged.
"""
from __future__ import annotations

import ast
import os
from typing import Dict, List, Optional, Set, Tuple

from types import SimpleNamespace

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
              ".pytest_cache", "build", "dist", ".apatch", "extracted"}


class NativeRefModel:
    """Cross-file reference graph keyed by ``"<def_rel>::<name>"`` symbol ids."""

    def __init__(self, defs: Dict[str, str], uses: Dict[str, List[Tuple[str, int]]]):
        self._defs = defs                      # sid -> defining rel_path
        self._uses = uses                      # rel_path -> [(sid, line), ...]
        self._refs: Dict[str, Set[str]] = {}   # sid -> {rel_path referencing}
        for rel, lst in uses.items():
            for sid, _ in lst:
                self._refs.setdefault(sid, set()).add(rel)

    def references(self, symbol: str) -> List[str]:
        return sorted(self._refs.get(symbol, ()))

    def references_in_file(self, rel_path: str) -> List[Tuple[str, int]]:
        return list(self._uses.get(rel_path, []))

    def definition_occurrence(self, rel_path: str, name: str) -> Optional[SimpleNamespace]:
        sid = rel_path + "::" + name
        if self._defs.get(sid) == rel_path:
            return SimpleNamespace(symbol=sid)  # resolve_symbol only reads .symbol
        return None


def _py_files(root: str) -> List[str]:
    out: List[str] = []
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.relpath(os.path.join(dp, f), root))
    return out


def _module_map(files: List[str]) -> Dict[str, str]:
    """``{dotted.module: rel_path}`` incl. package names for ``__init__.py``."""
    out: Dict[str, str] = {}
    for rel in files:
        mod = rel[:-3].replace(os.sep, ".")
        out[mod] = rel
        if mod.endswith(".__init__"):
            out[mod[:-9]] = rel
    return out


def native_reference_model(root: str) -> NativeRefModel:
    """Build the import-resolved reference graph for every ``.py`` under ``root``."""
    root = os.path.abspath(root)
    files = _py_files(root)
    mod_to_rel = _module_map(files)

    defs: Dict[str, str] = {}
    trees: Dict[str, ast.AST] = {}
    for rel in files:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError, ValueError):
            continue
        trees[rel] = tree
        for node in tree.body:  # top-level defs are the anchor granularity
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[rel + "::" + node.name] = rel

    uses: Dict[str, List[Tuple[str, int]]] = {}
    for rel, tree in trees.items():
        name_to_sid: Dict[str, str] = {}   # `from a import foo`  -> foo -> a.py::foo
        mod_aliases: Dict[str, str] = {}    # `import a as X`       -> X   -> a.py
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                drel = mod_to_rel.get(node.module)
                if drel:
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        sid = drel + "::" + alias.name
                        if sid in defs:
                            name_to_sid[alias.asname or alias.name] = sid
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    drel = mod_to_rel.get(alias.name)
                    if drel:
                        mod_aliases[alias.asname or alias.name] = drel

        file_uses: List[Tuple[str, int]] = []
        seen: Set[Tuple[str, int]] = set()
        for node in ast.walk(tree):
            sid: Optional[str] = None
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                sid = name_to_sid.get(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                drel = mod_aliases.get(node.value.id)
                if drel:
                    cand = drel + "::" + node.attr
                    if cand in defs:
                        sid = cand
            if sid and defs.get(sid) != rel:  # cross-file references only
                key = (sid, node.lineno - 1)
                if key not in seen:
                    seen.add(key)
                    file_uses.append((sid, node.lineno - 1))
        if file_uses:
            uses[rel] = file_uses
    return NativeRefModel(defs, uses)

