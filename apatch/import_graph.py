"""Import graph for Python and TypeScript/JavaScript (R44, R45)."""

from __future__ import annotations
from pathlib import Path

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from apatch.git_util import path_matches
from apatch.path_index import SKIP_DIR_NAMES, iter_target_files

_PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
    re.MULTILINE,
)
_TS_IMPORT = re.compile(
    r"""import\s+(?:type\s+)?(?:[\w*{}\s,]+\s+from\s+)?['"]([^'"]+)['"]""",
    re.MULTILINE,
)

_CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx"}


@dataclass
class ImportGraph:
    root: str
    edges: List[Tuple[str, str, str]] = field(default_factory=list)  # from_rel, to_rel, via
    _index: Dict[str, Set[str]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._index = {}
        for src, dst, _via in self.edges:
            self._index.setdefault(src, set()).add(dst)

    def neighbors(self, rel_path: str, depth: int = 1) -> Set[str]:
        """Files imported transitively from ``rel_path`` (forward)."""
        rel = rel_path.replace("\\", "/")
        seen: Set[str] = {rel}
        frontier = {rel}
        for _ in range(max(1, depth)):
            nxt: Set[str] = set()
            for node in frontier:
                for dst in self._index.get(node, set()):
                    if dst not in seen:
                        seen.add(dst)
                        nxt.add(dst)
            frontier = nxt
        seen.discard(rel)
        return seen

    def dependents(self, rel_path: str, depth: int = 1) -> Set[str]:
        """Files that import ``rel_path`` transitively (reverse impact)."""
        rel = rel_path.replace("\\", "/")
        reverse: Dict[str, Set[str]] = {}
        for src, dst, _ in self.edges:
            reverse.setdefault(dst, set()).add(src)
        seen: Set[str] = {rel}
        frontier = {rel}
        for _ in range(max(1, depth)):
            nxt: Set[str] = set()
            for node in frontier:
                for src in reverse.get(node, set()):
                    if src not in seen:
                        seen.add(src)
                        nxt.add(src)
            frontier = nxt
        seen.discard(rel)
        return seen


def _is_code_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _CODE_EXTS


def build_import_graph(
    target_dir: str,
    *,
    include_globs: Optional[List[str]] = None,
) -> ImportGraph:
    root = os.path.abspath(target_dir)
    module_map = _build_module_map(root)
    edges: List[Tuple[str, str, str]] = []

    for abs_path in iter_target_files(root):
        if not _is_code_file(abs_path):
            continue
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        if include_globs and not any(path_matches(rel, g) for g in include_globs):
            continue
        try:
            text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ext = os.path.splitext(abs_path)[1].lower()
        if ext == ".py":
            edges.extend(_python_edges(root, rel, text, module_map))
        else:
            edges.extend(_ts_edges(root, rel, text))

    return ImportGraph(root=root, edges=edges)


def _build_module_map(root: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for abs_path in iter_target_files(root):
        if not abs_path.endswith(".py"):
            continue
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        if rel.endswith("/__init__.py"):
            mod = rel[: -len("/__init__.py")].replace("/", ".")
        else:
            mod = rel[:-3].replace("/", ".")
        mapping[mod] = rel
        base = os.path.basename(rel)
        if base not in mapping:
            mapping[base] = rel
    return mapping


def _python_edges(
    root: str,
    from_rel: str,
    text: str,
    module_map: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    edges: List[Tuple[str, str, str]] = []
    from_dir = os.path.dirname(from_rel)
    for m in _PY_IMPORT.finditer(text):
        mod = m.group(1) or m.group(2) or ""
        if mod.startswith("."):
            dst = _resolve_relative_py(from_dir, mod, root)
        elif mod:
            dst = _resolve_python_module(mod, module_map)
        else:
            dst = None
        if dst and dst != from_rel:
            edges.append((from_rel, dst, "import"))
    return edges


def _resolve_python_module(mod: str, module_map: Dict[str, str]) -> Optional[str]:
    if mod in module_map:
        return module_map[mod]
    parts = mod.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in module_map:
            return module_map[candidate]
        parts.pop()
    tail = mod.split(".")[-1]
    return module_map.get(f"{tail}.py") or module_map.get(tail)


def _resolve_relative_py(from_dir: str, mod: str, root: str) -> Optional[str]:
    if not mod.startswith("."):
        return None
    dots = len(mod) - len(mod.lstrip("."))
    rest = mod[dots:].replace(".", "/")
    base_parts = [p for p in from_dir.split("/") if p]
    if dots > 1:
        trim = dots - 1
        if trim > len(base_parts):
            return None
        base_parts = base_parts[:-trim]
    base = "/".join(base_parts)
    if rest:
        candidate = f"{base}/{rest}.py" if base else f"{rest}.py"
    else:
        candidate = f"{base}/__init__.py" if base else None
    if candidate and os.path.isfile(os.path.join(root, candidate)):
        return candidate.replace("\\", "/")
    return None


def _ts_edges(root: str, from_rel: str, text: str) -> List[Tuple[str, str, str]]:
    edges: List[Tuple[str, str, str]] = []
    from_dir = os.path.dirname(from_rel)
    for m in _TS_IMPORT.finditer(text):
        spec = m.group(1)
        if not spec or spec.startswith("."):
            dst = _resolve_ts_relative(root, from_dir, spec)
        else:
            dst = None
        if dst and dst != from_rel:
            edges.append((from_rel, dst, "import"))
    return edges


def _resolve_ts_relative(root: str, from_dir: str, spec: str) -> Optional[str]:
    if not spec.startswith("."):
        return None
    base = os.path.normpath(os.path.join(from_dir, spec)).replace("\\", "/")
    candidates = [
        base,
        f"{base}.ts",
        f"{base}.tsx",
        f"{base}.js",
        f"{base}.py",
        f"{base}/index.ts",
        f"{base}/index.tsx",
        f"{base}/index.py",
    ]
    for rel in candidates:
        if os.path.isfile(os.path.join(root, rel)):
            return rel.replace("\\", "/")
    return None


def file_layer(rel_path: str, layer_paths: List[str]) -> bool:
    return any(path_matches(rel_path, p) for p in layer_paths)
