"""Lightweight symbol index for impact analysis (R45)."""

from __future__ import annotations
from pathlib import Path

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from apatch.path_index import iter_target_files

_PY_CLASS = re.compile(r"^class\s+(\w+)", re.MULTILINE)
_PY_DEF = re.compile(r"^def\s+(\w+)", re.MULTILINE)
_TS_EXPORT = re.compile(
    r"export\s+(?:type|interface|class|function|const)\s+(\w+)",
    re.MULTILINE,
)


@dataclass
class SymbolIndex:
    root: str
    definitions: Dict[str, str] = field(default_factory=dict)  # symbol -> rel path
    references: Dict[str, Set[str]] = field(default_factory=dict)  # symbol -> rel paths

    def files_referencing(self, symbol: str) -> Set[str]:
        return set(self.references.get(symbol, set()))


def build_symbol_index(target_dir: str) -> SymbolIndex:
    root = os.path.abspath(target_dir)
    index = SymbolIndex(root=root)

    for abs_path in iter_target_files(root):
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
            continue
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        try:
            text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        defs: Set[str] = set()
        if ext == ".py":
            defs.update(_PY_CLASS.findall(text))
            defs.update(_PY_DEF.findall(text))
        else:
            defs.update(_TS_EXPORT.findall(text))

        for name in defs:
            if name not in index.definitions:
                index.definitions[name] = rel

        for name in index.definitions:
            if re.search(rf"\b{re.escape(name)}\b", text) and name not in defs:
                index.references.setdefault(name, set()).add(rel)

    return index


def find_symbol_definition(index: SymbolIndex, symbol: str) -> Optional[str]:
    return index.definitions.get(symbol)
