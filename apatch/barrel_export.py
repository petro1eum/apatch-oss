"""Append re-exports to TypeScript barrel files (R23)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List


def emit_barrel_exports(
    barrel_path: str,
    exported_meta: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Append `export { X } from './relative'` lines if missing."""
    path = Path(barrel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    added: List[str] = []
    barrel_dir = path.parent.resolve()

    for meta in exported_meta:
        wh = meta.get("wiring_hints") or {}
        line = wh.get("barrel_export") or ""
        module_path = meta.get("module_path") or wh.get("target_module") or ""
        export_name = _export_name(meta, wh, module_path)
        if not export_name or not module_path:
            continue
        if not line:
            rel = _relative_module(barrel_dir, module_path)
            line = f"export {{ {export_name} }} from '{rel}';"
        if line.strip() in existing:
            continue
        existing = existing.rstrip() + ("\n" if existing else "") + line.strip() + "\n"
        added.append(line.strip())

    if added:
        path.write_text(existing, encoding="utf-8")
    return {"ok": True, "barrel_path": str(path), "added": added}


def revert_barrel_exports(
    barrel_path: Optional[str],
    added_lines: List[str],
) -> Dict[str, Any]:
    """Remove lines previously appended by emit_barrel_exports (R39)."""
    if not barrel_path or not added_lines:
        return {"ok": True, "barrel_path": barrel_path, "reverted": []}
    path = Path(barrel_path)
    if not path.is_file():
        return {"ok": True, "barrel_path": barrel_path, "reverted": []}

    remove_set = {line.strip() for line in added_lines if line and line.strip()}
    if not remove_set:
        return {"ok": True, "barrel_path": str(path), "reverted": []}

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept: List[str] = []
    reverted: List[str] = []
    for ln in lines:
        if ln.strip() in remove_set:
            reverted.append(ln.strip())
        else:
            kept.append(ln)
    if reverted:
        out = "".join(kept)
        if out and not out.endswith("\n"):
            out += "\n"
        path.write_text(out, encoding="utf-8")
    return {"ok": True, "barrel_path": str(path), "reverted": reverted}


def _export_name(meta: Dict[str, Any], wh: Dict[str, Any], module_path: str) -> str:
    exports = wh.get("exports") or meta.get("exports") or []
    if exports:
        return exports[0]
    import_line = wh.get("import_line") or ""
    m = re.search(r"\{\s*([^}]+)\s*\}", import_line)
    if m:
        return m.group(1).strip().split(",")[0].strip()
    base = os.path.splitext(os.path.basename(module_path))[0]
    if base.startswith("use"):
        return base
    return base


def _relative_module(barrel_dir: Path, module_path: str) -> str:
    target = Path(module_path)
    if not target.is_absolute():
        target = (barrel_dir / module_path).resolve()
    rel = os.path.relpath(target, barrel_dir).replace("\\", "/")
    for ext in (".ts", ".tsx"):
        if rel.endswith(ext):
            rel = rel[: -len(ext)]
            break
    if not rel.startswith("."):
        rel = "./" + rel
    return rel
