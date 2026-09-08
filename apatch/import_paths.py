"""Workspace-relative TypeScript import paths for auto-wiring."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


_SEMANTIC_RULE_NAMES = ("semantic-verify.yaml", "semantic-verify.yml")


def resolve_consumer_root(start_path: str) -> str:
    """Nearest package/tsconfig root with a ``src/`` tree (monorepo-safe)."""
    cur = Path(start_path).resolve()
    if cur.is_file():
        cur = cur.parent
    fallback = str(cur)
    nearest_src: Optional[Path] = None
    for _ in range(16):
        if (cur / "src").is_dir():
            nearest_src = cur
            if (cur / "package.json").exists() or (cur / "tsconfig.json").exists():
                return str(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    if nearest_src is not None:
        return str(nearest_src)
    return fallback


def find_semantic_rules_path(target_dir: str) -> Optional[str]:
    """Locate semantic-verify rules under consumer ``manifests/`` (monorepo-safe)."""
    consumer = resolve_consumer_root(target_dir)
    abs_target = os.path.abspath(target_dir)
    for base in (consumer, abs_target):
        for name in _SEMANTIC_RULE_NAMES:
            path = os.path.join(base, "manifests", name)
            if os.path.isfile(path):
                return path
    return None


def strip_ts_extension(path: str) -> str:
    for ext in (".tsx", ".ts", ".jsx", ".js"):
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


def workspace_ts_import(module_path: str, workspace: str, symbol: str) -> str:
    """Build `import { Symbol } from '@/…'` from an on-disk module path."""
    mp = Path(module_path).resolve()
    ws = Path(workspace).resolve()
    src_root = ws / "src"
    try:
        rel = mp.relative_to(src_root).as_posix()
        base = strip_ts_extension(rel)
        return f"import {{ {symbol} }} from '@/{base}';"
    except ValueError:
        return f"import {{ {symbol} }} from '{strip_ts_extension(mp.as_posix())}';"


def normalize_ts_import_line(import_line: str, workspace: str) -> str:
    """Rewrite absolute filesystem paths in import lines to @/ aliases when possible."""
    line = import_line.strip()
    if not line:
        return line
    ws = Path(workspace).resolve()
    src_root = ws / "src"

    m = re.match(
        r"""^(import\s+(?:type\s+)?\{[^}]+\}\s+from\s+['"])([^'"]+)(['"];?\s*)$""",
        line,
    )
    if not m:
        return line

    prefix, path_spec, suffix = m.group(1), m.group(2), m.group(3)
    if not os.path.isabs(path_spec):
        return line

    try:
        rel = Path(path_spec).resolve().relative_to(src_root.resolve()).as_posix()
        return f"{prefix}@/{strip_ts_extension(rel)}{suffix if suffix.endswith(';') else suffix + ';'}"
    except ValueError:
        return line
