"""Optional parent-file auto-wiring after strip + to-module (R28)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from apatch.import_paths import normalize_ts_import_line, workspace_ts_import
from apatch.strip import _is_early_return_guard_line


def split_hook_manifest_replace(replace: str) -> Dict[str, str]:
    """Split hook manifest replace into wiring (hook call + guard) and shell JSX tail."""
    if not replace.strip():
        return {"wiring": "", "shell": ""}
    lines = replace.splitlines()
    shell_start: Optional[int] = None
    passed_hook = False
    for i, ln in enumerate(lines):
        if re.search(r"\buse[A-Z]\w*\s*\(", ln):
            passed_hook = True
        if not passed_hook:
            continue
        stripped = ln.strip()
        if stripped.startswith("return (") or stripped == "return (":
            if _is_early_return_guard_line(lines, i):
                continue
            lookahead = "\n".join(lines[i : min(i + 6, len(lines))])
            if "<" in lookahead:
                shell_start = i
    if shell_start is None:
        return {"wiring": replace.rstrip() + "\n" if replace.strip() else "", "shell": ""}
    wiring = "\n".join(lines[:shell_start]).strip()
    shell = "\n".join(lines[shell_start:]).strip()
    return {
        "wiring": (wiring + "\n") if wiring else "",
        "shell": (shell + "\n") if shell else "",
    }


def _extract_notfound_guard(wiring_part: str, wire: str) -> str:
    match = re.search(
        r"(if\s*\([^)]+\)\s*\{\s*return\s*\([\s\S]*?\)\s*;\s*\})",
        wiring_part,
        flags=re.MULTILINE,
    )
    if match and match.group(1) not in wire:
        return match.group(1)
    return ""


def _format_hook_wire(ctx: Dict[str, Any], stub: str) -> str:
    split = split_hook_manifest_replace(stub)
    wiring = (split.get("wiring") or stub).strip()
    if _stub_has_hook_destructure(wiring):
        wire = _statement_wire_block(wiring).rstrip()
    else:
        wire = _format_wire(ctx).rstrip()
    guard = _extract_notfound_guard(split.get("wiring", ""), wire)
    shell = (split.get("shell") or "").rstrip()
    parts = [wire]
    if guard:
        parts.append(guard)
    if shell:
        parts.append(shell)
    return "\n".join(parts) + "\n"


def extract_jsx_wire_from_replace(replace: str, symbol: str) -> str:
    """Pull a <Symbol ... /> call out of a manifest replace stub (may include ternary glue)."""
    if not replace or not symbol:
        return ""
    match = re.search(rf"(<\s*{re.escape(symbol)}\b[\s\S]*?/>)", replace)
    return match.group(1).strip() if match else ""


def _stub_has_hook_destructure(stub: str) -> bool:
    """True when manifest replace already destructures a hook call."""
    return bool(
        re.search(r"const\s+\{[^}]+\}\s*=\s*use[A-Z]\w*\s*\(", stub, flags=re.DOTALL)
    )


def stub_already_wires_symbol(stub: str, symbol: str) -> bool:
    """True when the manifest stub already contains a wired component call with props."""
    if not stub or not symbol:
        return False
    if not re.search(rf"<\s*{re.escape(symbol)}\b", stub):
        return False
    return bool(re.search(rf"<\s*{re.escape(symbol)}\s+[^/>]", stub))


def apply_auto_wire(
    parent_path: str,
    exported_meta: List[Dict[str, Any]],
    *,
    specs_replace: Optional[List[str]] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Insert imports and wire extracted modules into the parent file.

    Supports:
    - stub replacement (manifest replace text)
    - insertion before until marker when replace is empty (typical JSX strip)
    """
    path = Path(parent_path)
    if not path.is_file():
        return {"ok": False, "error": f"parent not found: {parent_path}", "changes": []}

    ws = workspace or str(path.parent)
    content = path.read_text(encoding="utf-8")
    original = content
    changes: List[str] = []
    failures: List[str] = []

    for idx, meta in enumerate(exported_meta):
        ctx = _resolve_wire_context(meta, ws)
        stub = _stub_for_index(specs_replace, idx, meta)
        parent_import = ctx["parent_import"] or _infer_parent_import(stub, ctx, meta, ws)
        if parent_import and not _has_import_line(content, parent_import):
            content = _insert_import(content, parent_import)
            changes.append(f"added import: {parent_import[:80]}")

        if not ctx["parent_wire"] and not stub:
            continue

        wired = False

        symbol = ctx.get("export_symbol") or ""

        if stub:
            if stub in content and stub_already_wires_symbol(stub, symbol):
                wired = True
                changes.append("manifest stub already wires component; kept replace text")
            elif stub in content and _stub_has_hook_destructure(stub):
                wired = True
                changes.append("manifest stub already wires hook with destructuring; kept replace text")
            elif stub in content:
                replacement = (
                    _format_hook_wire(ctx, stub)
                    if ctx.get("module_kind") == "hook"
                    else _format_wire(ctx)
                )
                content = content.replace(stub, replacement, 1)
                wired = True
                changes.append("replaced stub with parent_wire")
            else:
                replaced = _replace_stub_fuzzy(content, stub, ctx)
                if replaced != content:
                    content = replaced
                    wired = True
                    changes.append("replaced stub (fuzzy) with parent_wire")

        if not wired and ctx.get("until_marker"):
            strip_shape = meta.get("strip_shape", "jsx")
            manifest_replace = (meta.get("stub_replacement") or "").strip()
            if strip_shape == "inner_component" and not manifest_replace:
                changes.append(
                    f"skipped until-marker wire for inner component {meta.get('label', idx)} (import only)"
                )
            elif ctx["parent_wire"].lstrip().startswith("<"):
                content, inserted = _insert_before_marker(content, ctx["until_marker"], ctx)
                if inserted:
                    wired = True
                    changes.append(f"inserted wire before until marker: {ctx['until_marker'][:40]}")

        if not wired and ctx.get("start_marker") and ctx["start_marker"] in content:
            content = content.replace(ctx["start_marker"], _format_wire(ctx).rstrip(), 1)
            wired = True
            changes.append("replaced start marker with parent_wire")

        if (
            not wired
            and meta.get("module_path")
            and meta.get("strip_shape") != "inner_component"
        ):
            failures.append(
                f"could not wire block {meta.get('label', idx)} "
                f"(no stub match and until marker {ctx.get('until_marker', '')!r} not found)"
            )

    if failures:
        return {"ok": False, "error": "; ".join(failures), "changes": changes, "changed": False}

    if content == original:
        return {"ok": True, "changed": False, "changes": changes}

    path.write_text(content, encoding="utf-8")
    return {"ok": True, "changed": True, "changes": changes}


def _infer_parent_import(
    stub: str,
    ctx: Dict[str, Any],
    meta: Dict[str, Any],
    workspace: str,
) -> str:
    """Synthesize parent import from hook symbol in stub or module path."""
    symbol = ctx.get("export_symbol") or ""
    if not symbol:
        match = re.search(r"\b(use[A-Z]\w*)\s*\(", stub or "")
        if match:
            symbol = match.group(1)
    module_path = (
        meta.get("module_path")
        or (meta.get("integration_hints") or {}).get("target_module")
        or (meta.get("wiring_hints") or {}).get("target_module")
        or ""
    )
    if symbol and module_path:
        line = workspace_ts_import(module_path, workspace, symbol)
        return normalize_ts_import_line(line, workspace)
    return ""


def _resolve_wire_context(meta: Dict[str, Any], workspace: str) -> Dict[str, Any]:
    wh = meta.get("wiring_hints") or {}
    ih = meta.get("integration_hints") or {}

    parent_wire = (ih.get("parent_wire") or wh.get("parent_wire") or "").strip()
    parent_wire = parent_wire.replace("{/* pass props */}", "").strip()
    # Collapse whitespace only for hook/statement wires; preserve multiline JSX.
    if not parent_wire.lstrip().startswith("<"):
        parent_wire = re.sub(r"\s+", " ", parent_wire)

    parent_import = (wh.get("parent_import") or ih.get("parent_import") or wh.get("import_line") or "").strip()
    module_kind = (ih.get("module_kind") or wh.get("module_kind") or "hook").lower()
    symbol = wh.get("export_symbol") or ""

    if not parent_import:
        module_path = meta.get("module_path") or ih.get("target_module") or ""
        if module_path and symbol:
            parent_import = workspace_ts_import(module_path, workspace, symbol)
    if workspace and parent_import:
        parent_import = normalize_ts_import_line(parent_import, workspace)

    return {
        "parent_import": parent_import,
        "parent_wire": parent_wire,
        "module_kind": module_kind,
        "export_symbol": symbol,
        "until_marker": meta.get("until_marker") or "",
        "start_marker": meta.get("start_marker") or "",
    }


def _stub_for_index(
    specs_replace: Optional[List[str]],
    idx: int,
    meta: Dict[str, Any],
) -> str:
    if specs_replace and idx < len(specs_replace):
        return specs_replace[idx] or ""
    return meta.get("stub_replacement") or ""


def _has_import_line(content: str, import_line: str) -> bool:
    line = import_line.strip()
    if line in content:
        return True
    symbol = _import_symbol(import_line)
    if not symbol:
        return False
    return bool(
        re.search(rf"import\s+(?:type\s+)?\{{[^}}]*\b{re.escape(symbol)}\b", content)
        or re.search(rf"import\s+{re.escape(symbol)}\b", content)
    )


def _import_symbol(import_line: str) -> str:
    m = re.search(r"import\s+(?:type\s+)?\{([^}]+)\}", import_line)
    if m:
        return m.group(1).split(",")[0].strip()
    m = re.match(r"import\s+(\w+)", import_line)
    return m.group(1) if m else import_line


def _insert_import(content: str, import_line: str) -> str:
    lines = content.splitlines(keepends=True)
    insert_at = _find_import_insert_index(lines)
    lines.insert(insert_at, import_line + "\n")
    return "".join(lines)


def _find_import_insert_index(lines: List[str]) -> int:
    """Insert after directives ('use client') and the top import block."""
    insert_at = 0
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        directive = stripped.strip("'\"")
        if directive in ("use client", "use server"):
            insert_at = i + 1
            i += 1
            continue
        break
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("import "):
            insert_at = i + 1
            i += 1
            while i < len(lines) and "} from " not in lines[i]:
                i += 1
            if i < len(lines):
                insert_at = i + 1
                i += 1
            continue
        break
    return insert_at


def _format_wire(ctx: Dict[str, Any]) -> str:
    wire = ctx["parent_wire"]
    module_kind = ctx.get("module_kind", "hook")
    if module_kind == "component" or wire.lstrip().startswith("<"):
        return _jsx_wire_block(wire)
    return _statement_wire_block(wire)


def _jsx_wire_block(parent_wire: str) -> str:
    wire = parent_wire.strip()
    if not wire.endswith("\n"):
        wire += "\n"
    if not wire.startswith(" "):
        wire = "      " + wire
    return wire


def _statement_wire_block(parent_wire: str) -> str:
    wire = parent_wire.rstrip()
    if not wire.endswith("\n"):
        wire += "\n"
    if not wire.startswith("  "):
        wire = "  " + wire.replace("\n", "\n  ")
    return wire


def _insert_before_marker(content: str, marker: str, ctx: Dict[str, Any]) -> tuple[str, bool]:
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if marker in line:
            lines.insert(i, _format_wire(ctx))
            return "".join(lines), True
    return content, False


def _replace_stub_fuzzy(content: str, stub: str, ctx: Dict[str, Any]) -> str:
    stub_lines = [ln.strip() for ln in stub.splitlines() if ln.strip()]
    if not stub_lines:
        return content
    pattern = re.escape(stub_lines[0])
    for extra in stub_lines[1:]:
        pattern += r"\s*" + re.escape(extra)
    match = re.search(pattern, content, flags=re.MULTILINE)
    if not match:
        return content
    replacement = (
        _format_hook_wire(ctx, stub)
        if ctx.get("module_kind") == "hook"
        else _format_wire(ctx)
    )
    return content[: match.start()] + replacement + content[match.end() :]
