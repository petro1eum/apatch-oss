"""Convert stripped TS/React fragments into scaffolded modules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from apatch.imports_resolver import (
    build_symbol_type_map,
    filter_imports_for_content,
    infer_param_types,
)

_TS_KEYWORDS = {
    "const", "let", "var", "function", "return", "if", "else", "for", "while",
    "true", "false", "null", "undefined", "async", "await", "import", "export",
    "from", "type", "interface", "new", "this", "typeof", "void", "switch",
    "case", "break", "continue", "default", "try", "catch", "finally", "throw",
    "class", "extends", "implements", "public", "private", "protected", "readonly",
    "as", "in", "of", "do", "delete", "instanceof", "yield", "enum",
}

_TS_BUILTINS = {
    "console", "Math", "JSON", "Object", "Array", "String", "Number", "Date",
    "Promise", "Map", "Set", "Error", "RegExp", "parseInt", "parseFloat",
    "isNaN", "Boolean", "Symbol", "BigInt", "Intl", "Reflect", "Proxy",
    "useState", "useEffect", "useCallback", "useMemo", "useRef", "useContext",
    "useReducer", "useLayoutEffect", "useDispatch", "useSelector", "React", "Fragment",
}

_TS_TYPE_NAMES = {
    "string", "boolean", "number", "null", "undefined", "any", "unknown", "never",
    "void", "object", "symbol", "bigint", "Record", "Partial", "Pick", "Omit",
}


@dataclass
class BlockAnalysis:
    exports: List[str] = field(default_factory=list)
    closure_params: List[str] = field(default_factory=list)
    param_types: Dict[str, str] = field(default_factory=dict)
    imports: List[str] = field(default_factory=list)


def _pascal(name: str) -> str:
    parts = re.split(r"[_\-\s]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _camel(name: str) -> str:
    p = _pascal(name)
    return p[:1].lower() + p[1:] if p else "extracted"


def extract_hook_name_from_text(text: str) -> Optional[str]:
    """Parse `useFoo(` from manifest replace / parent_wire."""
    if not text:
        return None
    m = re.search(r"\b(use[A-Z][\w$]*)\s*\(", text)
    return m.group(1) if m else None


def resolve_hook_export_name(
    *,
    label: str = "",
    target_module: str = "",
    manifest_replace: str = "",
    parent_wire: str = "",
) -> str:
    """Stable hook export name: manifest > target path > label (strip trailing _hook)."""
    for blob in (manifest_replace, parent_wire):
        found = extract_hook_name_from_text(blob)
        if found:
            return found
    if target_module:
        stem = Path(target_module).stem
        if stem.startswith("use") and len(stem) > 3:
            return stem
    parts = re.split(r"[_\-\s]+", label or "extracted")
    if parts and parts[-1].lower() == "hook":
        parts = parts[:-1]
    base = "".join(p[:1].upper() + p[1:] for p in parts if p) or "Extracted"
    return f"use{base}"


def resolve_component_export_name(
    *,
    label: str = "",
    target_module: str = "",
    manifest_replace: str = "",
) -> str:
    """Component export: target path stem > PascalCase(label)."""
    if target_module:
        stem = Path(target_module).stem
        if stem and stem[0].isupper():
            return stem
    for blob in (manifest_replace,):
        m = re.search(r"<\s*([A-Z][\w$]*)\b", blob)
        if m:
            return m.group(1)
    return _pascal(label or "extracted")


def hook_params_interface_name(hook_name: str) -> str:
    if hook_name.startswith("use") and len(hook_name) > 3:
        return f"Use{hook_name[3:]}Params"
    return f"{hook_name[:1].upper()}{hook_name[1:]}Params"


def classify_strip_shape(content: str) -> str:
    """Classify removed block for auto-wire policy."""
    s = content.lstrip()
    if re.match(r"(?:const|function)\s+\w+", s) and "=>" in s[:400]:
        return "inner_component"
    if re.match(r"<\s*[A-Za-z]", s) or re.search(r"\bchildren\s*:", s):
        return "jsx"
    return "logic"


def _parse_binding_names(fragment: str) -> List[str]:
    """Split `a, b: Type, c = default` destructuring fragments into binding names."""
    names: List[str] = []
    for part in fragment.split(","):
        part = part.strip()
        if not part:
            continue
        part = part.split("=")[0].strip()
        if ":" in part:
            part = part.split(":")[0].strip()
        m = re.match(r"^([a-zA-Z_$][\w$]*)$", part)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def _parse_param_list(params: str) -> List[str]:
    names: List[str] = []
    for part in params.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([a-zA-Z_$][\w$]*)", part)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def _collect_destructure_bindings(content: str) -> List[str]:
    names: List[str] = []
    for pat in (
        r"\b(?:const|let|var)\s+\[\s*([^\]]+)\]\s*=",
        r"\b(?:const|let|var)\s+\{\s*([^}]+)\}\s*=",
    ):
        for m in re.finditer(pat, content):
            for name in _parse_binding_names(m.group(1)):
                if name not in names:
                    names.append(name)
    return names


def _collect_callback_param_bindings(content: str) -> Set[str]:
    names: Set[str] = set()
    for pat in (
        r"\.(?:find|filter|map|forEach|some|every|reduce)\s*\(\s*\(?([^)]*)\)?\s*=>",
        r"(?:async\s+)?\(\s*([^)]*)\s*\)\s*=>",
    ):
        for m in re.finditer(pat, content):
            for name in _parse_param_list(m.group(1)):
                names.add(name)
    for m in re.finditer(r"catch\s*\(\s*([a-zA-Z_$][\w$]*)\s*\)", content):
        names.add(m.group(1))
    return names


def _object_literal_property_keys(content: str) -> Set[str]:
    keys: Set[str] = set()
    for m in re.finditer(r"\{([^{}]*)\}", content):
        blob = m.group(1)
        for part in blob.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            key = part.split(":", 1)[0].strip()
            if re.match(r"^[a-zA-Z_$][\w$]*$", key):
                keys.add(key)
    return keys


def extract_defined_names(content: str) -> List[str]:
    names: List[str] = []
    patterns = [
        r"\bconst\s+([a-zA-Z_$][\w$]*)\s*=",
        r"\blet\s+([a-zA-Z_$][\w$]*)\s*=",
        r"\bvar\s+([a-zA-Z_$][\w$]*)\s*=",
        r"\bfunction\s+([a-zA-Z_$][\w$]*)\s*\(",
        r"\basync\s+function\s+([a-zA-Z_$][\w$]*)\s*\(",
        r"\b([a-zA-Z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
        r"\.map\s*\(\s*\(?\s*([a-zA-Z_$][\w$]*)\s*\)?\s*=>",
        r"\.forEach\s*\(\s*\(?\s*([a-zA-Z_$][\w$]*)\s*\)?\s*=>",
    ]
    for pat in patterns:
        for m in re.finditer(pat, content):
            name = m.group(1)
            if name not in names:
                names.append(name)
    for name in _collect_destructure_bindings(content):
        if name not in names:
            names.append(name)
    return names


def collect_scope_bindings(content: str) -> Set[str]:
    """All bindings in a block: top-level defs, destructuring, and callback params."""
    names = set(extract_defined_names(content))
    names.update(_collect_callback_param_bindings(content))
    return names


def extract_top_level_hook_exports(content: str) -> List[str]:
    """Hook return surface: top-level bindings only (exclude nested handler locals)."""
    if not content.strip():
        return []
    lines = content.splitlines()
    min_indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    names: List[str] = []
    depth = 0
    for line in lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent > min_indent or depth > 0:
            depth += line.count("{") - line.count("}")
            continue
        chunk = line.strip()
        for pat in (
            r"^(?:const|let|var)\s+([a-zA-Z_$][\w$]*)\s*=",
            r"^(?:const|let|var)\s+\[\s*([^\]]+)\]\s*=",
            r"^(?:const|let|var)\s+\{\s*([^}]+)\}\s*=",
            r"^async\s+function\s+([a-zA-Z_$][\w$]*)\s*\(",
            r"^function\s+([a-zA-Z_$][\w$]*)\s*\(",
            r"^([a-zA-Z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
        ):
            m = re.match(pat, chunk)
            if not m:
                continue
            if "[" in pat or "{" in pat:
                for name in _parse_binding_names(m.group(1)):
                    if name not in names:
                        names.append(name)
            elif m.group(1) not in names:
                names.append(m.group(1))
            break
        depth += line.count("{") - line.count("}")
    return names


def extract_hook_call_params(replace: str, hook_name: str) -> List[str]:
    if not replace or not hook_name:
        return []
    m = re.search(
        rf"{re.escape(hook_name)}\s*\(\s*\{{\s*([^}}]+)\}}\s*\)",
        replace,
    )
    if not m:
        return []
    return _parse_binding_names(m.group(1))


def hook_call_has_no_args(replace: str, hook_name: str) -> bool:
    """True when manifest wires the hook with an empty call: useFoo()."""
    if not replace or not hook_name:
        return False
    return bool(re.search(rf"{re.escape(hook_name)}\s*\(\s*\)", replace))


def extract_hook_destructure_exports(replace: str, hook_name: str) -> List[str]:
    if not replace or not hook_name:
        return []
    m = re.search(
        rf"const\s+\{{\s*([^}}]+)\}}\s*=\s*{re.escape(hook_name)}\b",
        replace,
    )
    if not m:
        return []
    return _parse_binding_names(m.group(1))


def extract_parent_scope_bindings(parent_content: str) -> Set[str]:
    """Symbols already defined in the parent component (props, outer state)."""
    names: Set[str] = set()
    for m in re.finditer(r"=\s*\(\s*\{([^}]*)\}\s*\)\s*=>", parent_content):
        names.update(_parse_binding_names(m.group(1)))
    for m in re.finditer(r"^\s*const\s+\[\s*([^\]]+)\]\s*=", parent_content, re.M):
        names.update(_parse_binding_names(m.group(1)))
    for m in re.finditer(r"^\s*const\s+\{\s*([^}]+)\}\s*=", parent_content, re.M):
        names.update(_parse_binding_names(m.group(1)))
    for m in re.finditer(r"^\s*const\s+([a-zA-Z_$][\w$]*)\s*=", parent_content, re.M):
        names.add(m.group(1))
    return names


def extract_closure_params(
    removed_content: str,
    parent_content: str = "",
    parent_imports: Optional[List[str]] = None,
    file_path: str = "",
) -> Set[str]:
    """Symbols used in block but not defined inside it (excluding imports)."""
    defined = collect_scope_bindings(removed_content)
    used = set(re.findall(r"\b([a-zA-Z_$][\w$]*)\b", removed_content))
    used -= _TS_KEYWORDS
    used -= _TS_BUILTINS
    used -= _TS_TYPE_NAMES
    used -= defined
    used -= _object_literal_property_keys(removed_content)
    used = {u for u in used if not (u[0].isupper() and u not in defined)}
    candidates = used
    if parent_content:
        parent_defined = extract_parent_scope_bindings(parent_content)
        return candidates & parent_defined
    # JSX fragments: collect external refs from expressions and attribute bindings.
    if re.search(r"<\s*[A-Za-z]", removed_content):
        jsx_refs: Set[str] = set()
        for m in re.finditer(r"\{!*([A-Za-z_$][\w$]*)\s*[\.\[]", removed_content):
            root = m.group(1)
            if root not in _TS_KEYWORDS and root not in _TS_BUILTINS:
                jsx_refs.add(root)
        for m in re.finditer(r"\{([$A-Za-z_][\w$]*(?:\.[$\w]+)*)\}", removed_content):
            root = m.group(1).split(".")[0]
            if root not in _TS_KEYWORDS and root not in _TS_BUILTINS:
                jsx_refs.add(root)
        for m in re.finditer(r"=\{([A-Za-z_$][\w$]*)\}", removed_content):
            root = m.group(1)
            if root not in _TS_KEYWORDS and root not in _TS_BUILTINS:
                jsx_refs.add(root)
        for m in re.finditer(r"\{([^{}]+)\}", removed_content):
            for ident in re.findall(r"\b([a-z_$][\w$]*)\b", m.group(1)):
                if ident not in _TS_KEYWORDS and ident not in _TS_BUILTINS:
                    jsx_refs.add(ident)
        used = jsx_refs - defined
    if parent_imports:
        from apatch.imports_resolver import parse_import_bindings

        imported: Set[str] = set()
        for imp in parent_imports:
            for local, _mod in parse_import_bindings(imp):
                imported.add(local)
        used -= imported
    return used


def extract_manifest_wire_params(replace: str) -> List[str]:
    """Prop names from an explicit manifest replace stub (e.g. monthStats={monthStats})."""
    if not replace:
        return []
    return sorted(set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*=\s*\{", replace)))


def extract_manifest_prop_aliases(replace: str) -> Dict[str, str]:
    """Map prop name -> parent symbol when they differ (onEditPeriod={handleEditPeriod})."""
    aliases: Dict[str, str] = {}
    for prop, value in re.findall(r"\b([A-Za-z_$][\w$]*)\s*=\s*\{([A-Za-z_$][\w$]*)\}", replace):
        if prop != value:
            aliases[prop] = value
    return aliases


def _format_props_destructure(params: List[str], manifest_replace: str) -> str:
    aliases = extract_manifest_prop_aliases(manifest_replace)
    parts: List[str] = []
    for p in params:
        if p in aliases:
            parts.append(f"{p}: {aliases[p]}")
        else:
            parts.append(p)
    return f"  const {{ {', '.join(parts)} }} = props;\n"


def analyze_block(
    removed_content: str,
    parent_content: str,
    parent_imports: List[str],
    file_path: str,
    *,
    manifest_replace: str = "",
    hook_name: str = "",
) -> BlockAnalysis:
    hook_call_params = extract_hook_call_params(manifest_replace, hook_name)
    hook_exports = extract_hook_destructure_exports(manifest_replace, hook_name)
    manifest_params = extract_manifest_wire_params(manifest_replace)
    is_component_manifest = bool(manifest_params and not hook_name)

    if hook_exports:
        exports = hook_exports
    elif is_component_manifest:
        exports = []
    elif hook_call_params and not hook_exports:
        # Side-effect hook: parent_wire is `useHook({ params })` with no destructure.
        exports = []
    else:
        exports = extract_top_level_hook_exports(removed_content) or extract_defined_names(removed_content)

    if hook_call_params:
        closure = hook_call_params
    elif hook_name and hook_call_has_no_args(manifest_replace, hook_name):
        closure = []
    elif is_component_manifest:
        closure = manifest_params
    elif manifest_params:
        closure = sorted(set(manifest_params) - set(exports))
    else:
        closure = sorted(
            set(extract_closure_params(removed_content, parent_content, parent_imports, file_path))
            - set(exports)
        )
    param_types = infer_param_types(closure, parent_content, file_path)
    imports = filter_imports_for_content(
        parent_imports, removed_content, parent_content, file_path
    )
    return BlockAnalysis(
        exports=exports,
        closure_params=closure,
        param_types=param_types,
        imports=imports,
    )


def _rewrite_body_for_params(body: str, params: List[str]) -> str:
    if not params:
        return body
    destructure = ", ".join(params)
    prefix = f"  const {{ {destructure} }} = params;\n"
    return prefix + body


def _sanitize_ts_type(ts_type: str) -> str:
    t = ts_type.strip().rstrip(";")
    missing = t.count("<") - t.count(">")
    if missing > 0:
        t += ">" * missing
    return t


def _format_params_interface(hook_name: str, analysis: BlockAnalysis) -> str:
    iface = hook_params_interface_name(hook_name)
    if not analysis.closure_params:
        return f"export interface {iface} {{\n  // no external dependencies detected\n}}\n"
    lines = []
    for p in analysis.closure_params:
        ts_type = _sanitize_ts_type(analysis.param_types.get(p, "unknown"))
        lines.append(f"  {p}: {ts_type};")
    return f"export interface {iface} {{\n" + "\n".join(lines) + "\n}\n"


def _format_return(exports: List[str]) -> str:
    if not exports:
        return "  return {};\n"
    items = ", ".join(exports)
    return f"  return {{ {items} }};\n"


def _consume_brace_block(lines: List[str], start: int) -> tuple[str, int]:
    depth = 0
    i = start
    while i < len(lines):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth == 0 and i > start:
            return "".join(lines[start : i + 1]), i - start + 1
        i += 1
    return "".join(lines[start:]), len(lines) - start


def _strip_if_guard_jsx_returns(body: str) -> str:
    """Drop ``if (...) { return (<jsx>); }`` guards — they belong in the parent shell."""
    lines = body.splitlines(keepends=True)
    out: List[str] = []
    i = 0
    while i < len(lines):
        if re.match(r"^\s*if\s*\([^)]+\)\s*\{", lines[i]):
            block, consumed = _consume_brace_block(lines, i)
            if re.search(r"return\s*\(\s*<", block):
                i += consumed
                continue
            out.extend(lines[i : i + consumed])
            i += consumed
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)


def _strip_trailing_jsx_return(body: str) -> str:
    """Remove a trailing ``return (<jsx>...)`` fragment leaked from manifest replace."""
    match = re.search(r"^\s*return\s*\(\s*<", body, flags=re.MULTILINE)
    if not match:
        return body
    return body[: match.start()].rstrip() + "\n"


def _strip_apatch_sentinel(body: str) -> str:
    return re.sub(r"^\s*// apatch:[^\n]*\n", "", body, flags=re.MULTILINE)


def _append_until_closure(body: str, until_marker: str) -> str:
    """Re-attach ``until`` line when strip keeps the closing line in the parent."""
    until = (until_marker or "").strip()
    if not until:
        return body
    if not re.search(r"\},\s*\[", until) and not until.endswith("});"):
        return body
    body_stripped = body.rstrip()
    if until in body_stripped:
        return body
    if not re.search(r"\buseEffect\s*\(", body):
        return body
    indent = ""
    match = re.search(r"^(\s*)useEffect", body, flags=re.MULTILINE)
    if match:
        indent = match.group(1)
    closing = until if until.startswith(indent) else indent + until.lstrip()
    return body_stripped + "\n" + closing + "\n"


def _is_effect_only_hook(body: str, exports: List[str]) -> bool:
    if exports:
        return False
    return bool(re.search(r"\buseEffect\s*\(", body))


def _sanitize_hook_body(body: str) -> str:
    cleaned = _strip_if_guard_jsx_returns(body)
    cleaned = _strip_trailing_jsx_return(cleaned)
    return cleaned.rstrip() + ("\n" if cleaned.strip() else "")


def _balanced_region(
    content: str,
    start: int,
    open_ch: str,
    close_ch: str,
    *,
    initial_depth: int = 0,
) -> Optional[str]:
    depth = initial_depth
    i = start
    while i < len(content):
        ch = content[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return content[start:i]
        i += 1
    return None


def extract_tab_entry_children(content: str) -> Optional[str]:
    """Pull JSX from a tab/array entry: `children: ( <jsx> )`."""
    match = re.search(r"\bchildren\s*:\s*\(\s*", content)
    if not match:
        match = re.search(r"\bchildren\s*:\s*(<)", content)
        if not match:
            return None
        jsx = _balanced_jsx_element(content, match.start(1))
        return jsx.strip() if jsx else None
    inner = _balanced_region(content, match.end(), "(", ")", initial_depth=1)
    return inner.strip() if inner is not None else None


def _balanced_jsx_element(content: str, start: int) -> Optional[str]:
    """Best-effort match for a single JSX element starting at `<`."""
    tag_match = re.match(r"<\s*([A-Za-z][\w$]*)", content[start:])
    if not tag_match:
        return None
    tag = tag_match.group(1)
    self_closing = re.search(rf"<\s*{re.escape(tag)}\b[^>]*/>", content[start:])
    if self_closing:
        return content[start : start + self_closing.end()]
    open_pat = rf"<\s*{re.escape(tag)}\b"
    close_pat = rf"</\s*{re.escape(tag)}\s*>"
    opens = list(re.finditer(open_pat, content[start:]))
    closes = list(re.finditer(close_pat, content[start:]))
    if not opens or not closes:
        return None
    return content[start : start + closes[-1].end()]


def extract_arrow_function_return_jsx(content: str) -> Optional[str]:
    """Pull JSX returned from `const fn = (...) => { return ( <jsx> ); }`."""
    if "=>" not in content:
        return None
    for match in re.finditer(r"return\s*\(\s*", content):
        inner = _balanced_region(content, match.end(), "(", ")", initial_depth=1)
        if inner and re.search(r"<\s*[A-Za-z]", inner):
            return inner.strip()
    match = re.search(r"=>\s*\(\s*", content)
    if match and re.search(r"<\s*[A-Za-z]", content[match.end() :]):
        inner = _balanced_region(content, match.end(), "(", ")", initial_depth=1)
        if inner is not None:
            return inner.strip()
    return None


def extract_root_jsx_element(content: str) -> Optional[str]:
    """Pull the outermost JSX element when the strip block starts with markup."""
    if not re.match(r"<\s*[A-Za-z]", content.lstrip()):
        return None
    match = re.search(r"<\s*[A-Za-z]", content)
    if not match:
        return None
    element = _balanced_jsx_element(content, match.start())
    return element.strip() if element else None


def extract_component_jsx_body(removed_content: str) -> str:
    """Normalize a stripped fragment to raw JSX for component conversion."""
    content = removed_content.rstrip()
    # Top-level JSX strips (e.g. table panels): keep the root element, not nested
    # `return (` bodies inside `.map()` / event handlers.
    if re.match(r"<\s*[A-Za-z]", content.lstrip()):
        root = extract_root_jsx_element(content)
        return root if root else content
    for extractor in (extract_tab_entry_children, extract_arrow_function_return_jsx):
        extracted = extractor(content)
        if extracted:
            return extracted
    return content


def _sanitize_component_body(body: str) -> str:
    """Drop structural ternary glue accidentally captured by start/until markers."""
    lines = body.splitlines()
    while lines and re.match(r"^\s*\{[^}]*\?\s*\(\s*$", lines[0]):
        lines = lines[1:]
    while lines and re.match(r"^\s*\)\s*:\s*\(\s*$", lines[-1]):
        lines = lines[:-1]
    if lines and lines[-1].strip() == ")":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _build_component_parent_wire(comp_name: str, params: List[str]) -> str:
    if not params:
        return f"<{comp_name} />"
    if len(params) <= 4:
        return f"<{comp_name} {' '.join(f'{p}={{{p}}}' for p in params)} />"
    prop_lines = "\n".join(f"              {p}={{{p}}}" for p in params)
    return f"<{comp_name}\n{prop_lines}\n            />"


def _build_parent_wire(hook_name: str, analysis: BlockAnalysis) -> str:
    if not analysis.closure_params:
        return f"const handlers = {hook_name}({{}});"
    args = ", ".join(f"{p}" for p in analysis.closure_params)
    return f"const {{ {', '.join(analysis.exports) or '/* handlers */'} }} = {hook_name}({{ {args} }});"


def convert_hook(
    *,
    removed_content: str,
    label: str,
    parent_imports: List[str],
    out_path: str,
    parent_content: str = "",
    file_path: str = "",
    manifest_replace: str = "",
    parent_wire: str = "",
    until_marker: str = "",
) -> dict:
    hook_name = resolve_hook_export_name(
        label=label,
        target_module=out_path,
        manifest_replace=manifest_replace,
        parent_wire=parent_wire,
    )
    params_iface = hook_params_interface_name(hook_name)
    analysis = analyze_block(
        removed_content,
        parent_content,
        parent_imports,
        file_path,
        manifest_replace=manifest_replace,
        hook_name=hook_name,
    )

    import_block = "\n".join(analysis.imports)
    if re.search(r"\bdispatch\s*\(", removed_content) and "AppDispatch" not in import_block:
        import_block = "import { AppDispatch } from '@/store';\n" + import_block
    if import_block:
        import_block += "\n\n"

    body = _sanitize_hook_body(removed_content)
    body = _strip_apatch_sentinel(body)
    body = _append_until_closure(body, until_marker)
    body = _rewrite_body_for_params(body, analysis.closure_params)

    manifest_exports = extract_hook_destructure_exports(manifest_replace, hook_name)
    if manifest_exports:
        return_suffix = _format_return(manifest_exports)
    elif analysis.exports:
        return_suffix = _format_return(analysis.exports)
        if return_suffix and re.search(r"\buseEffect\s*\(", body):
            # Heuristic only when exports were inferred from body, not manifest.
            return_suffix = ""
    elif _is_effect_only_hook(body, analysis.exports):
        return_suffix = ""
    else:
        return_suffix = ""

    if analysis.closure_params:
        params_block = f"{_format_params_interface(hook_name, analysis)}\n"
        fn_sig = f"export function {hook_name}(params: {params_iface}) {{\n"
    else:
        params_block = ""
        fn_sig = f"export function {hook_name}() {{\n"

    content = (
        f"{import_block}"
        f"{params_block}"
        f"{fn_sig}"
        f"{_indent(body, 2)}"
        f"{return_suffix}"
        f"}}\n"
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(content, encoding="utf-8")
    return {
        "module_path": out_path,
        "export_name": hook_name,
        "module_kind": "hook",
        "exports": analysis.exports,
        "closure_params": analysis.closure_params,
        "parent_wire": _build_parent_wire(hook_name, analysis),
    }


def convert_component(
    *,
    removed_content: str,
    label: str,
    parent_imports: List[str],
    out_path: str,
    parent_content: str = "",
    file_path: str = "",
    manifest_replace: str = "",
) -> dict:
    comp_name = resolve_component_export_name(
        label=label,
        target_module=out_path,
        manifest_replace=manifest_replace,
    )
    jsx_body = extract_component_jsx_body(removed_content)
    analysis = analyze_block(
        jsx_body,
        parent_content,
        parent_imports,
        file_path,
        manifest_replace=manifest_replace,
    )

    import_block = "\n".join(analysis.imports)
    if import_block:
        import_block += "\n\n"
    if re.search(r"\bReact\.", removed_content) and "import React" not in import_block:
        import_block = "import React from 'react';\n" + import_block
    if (
        ("PeriodMetrics" in parent_content or "monthStats" in jsx_body)
        and "PeriodMetrics" not in import_block
    ):
        import_block = "import { PeriodMetrics } from '@/domain/planning/quotaAnalytics';\n" + import_block
    if analysis.closure_params and "can" in analysis.closure_params and "Permission" not in import_block:
        import_block = "import { Permission } from '@/types/users';\n" + import_block
    if "MONTH_NAMES" in jsx_body and "MONTH_NAMES" not in import_block:
        import_block = (
            "import { MONTH_NAMES } from '@/domain/planning/quotaAnalytics';\n" + import_block
        )

    props_preview = manifest_replace + jsx_body
    sales_symbols = [
        sym
        for sym in ("GreenSheet", "ClientDeal", "Client")
        if (
            sym in props_preview
            or any(sym in analysis.param_types.get(p, "") for p in analysis.closure_params)
        )
        and sym not in import_block
    ]
    if sales_symbols:
        import_block = (
            f"import {{ {', '.join(sales_symbols)} }} from '@/types/sales';\n" + import_block
        )
    contact_symbols = [
        sym
        for sym in ("Contact", "DealContactContext")
        if (
            sym in props_preview
            or any(sym in analysis.param_types.get(p, "") for p in analysis.closure_params)
        )
        and sym not in import_block
    ]
    if contact_symbols:
        import_block = (
            f"import {{ {', '.join(contact_symbols)} }} from '@/types/contacts';\n" + import_block
        )
    if "<Check " in jsx_body and "Check" not in import_block:
        import_block = "import { Check } from 'lucide-react';\n" + import_block
    if re.search(r"<\s*Card\b", jsx_body) and "Card" not in import_block:
        import_block = (
            "import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';\n"
            + import_block
        )
    if re.search(r"\bnavigate\s*\(", jsx_body) and "useNavigate" not in import_block:
        import_block = "import { useNavigate } from 'react-router-dom';\n" + import_block
    if re.search(r"<\s*Input\b", jsx_body) and "Input" not in import_block:
        import_block = "import { Input } from '@/components/ui/input';\n" + import_block
    if re.search(r"<\s*Button\b", jsx_body) and "Button" not in import_block:
        import_block = "import { Button } from '@/components/ui/button';\n" + import_block
    if re.search(r"<\s*Table\b", jsx_body) and "Table" not in import_block:
        import_block = (
            "import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } "
            "from '@/components/ui/table';\n" + import_block
        )
    if "import React" not in import_block and re.search(r"<\s*[A-Za-z]", jsx_body):
        import_block = "import React from 'react';\n" + import_block

    props_lines = []
    for p in analysis.closure_params:
        ts_type = _sanitize_ts_type(analysis.param_types.get(p, "unknown"))
        props_lines.append(f"  {p}: {ts_type};")
    props_iface = (
        f"export interface {comp_name}Props {{\n" + "\n".join(props_lines or ["  // define props"]) + "\n}\n"
    )

    body = _sanitize_component_body(jsx_body.rstrip())
    body = re.sub(r"^\s*\{/\*[\s\S]*?\*/\}\s*", "", body).strip()
    if "PeriodMetrics" in parent_content or "monthStats" in body:
        body = re.sub(
            r"\.map\(\(\s*([a-zA-Z_$][\w$]*)\s*\)\s*=>",
            r".map((\1: PeriodMetrics) =>",
            body,
            count=1,
        )

    # If block looks like JSX return, keep it; else wrap return null
    has_jsx = "<" in body and re.search(r"<\s*[A-Za-z]", body)
    starts_with_jsx = bool(re.match(r"^\s*<", body))
    if has_jsx and starts_with_jsx:
        body = _indent(body, 2) + "\n"
        closing = f"  return (\n    <>\n{body}    </>\n  );\n"
    elif "return" in body:
        closing = _indent(body, 2) + "\n"
    else:
        closing = _indent(body, 2) + "\n  return null;\n"

    navigate_hook = ""
    if re.search(r"\bnavigate\s*\(", jsx_body):
        navigate_hook = "  const navigate = useNavigate();\n"

    destructure = ""
    if analysis.closure_params:
        destructure = _format_props_destructure(analysis.closure_params, manifest_replace)
        fn_sig = f"(props: {comp_name}Props)"
    else:
        fn_sig = "()"

    content = (
        f"{import_block}"
        f"{props_iface}\n"
        f"export const {comp_name}: React.FC<{comp_name}Props> = {fn_sig} => {{\n"
        f"{destructure}"
        f"{navigate_hook}"
        f"{closing}"
        f"}};\n"
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(content, encoding="utf-8")
    return {
        "module_path": out_path,
        "export_name": comp_name,
        "module_kind": "component",
        "exports": analysis.exports,
        "closure_params": analysis.closure_params,
        "parent_wire": _build_component_parent_wire(comp_name, analysis.closure_params),
    }


def convert_util(
    *,
    removed_content: str,
    label: str,
    parent_imports: List[str],
    out_path: str,
    parent_content: str = "",
    file_path: str = "",
) -> dict:
    fn_name = _camel(label or "extractedUtil")
    analysis = analyze_block(removed_content, parent_content, parent_imports, file_path)

    import_block = "\n".join(analysis.imports)
    if import_block:
        import_block += "\n\n"

    if analysis.closure_params:
        params_sig = ", ".join(
            f"{p}: {analysis.param_types.get(p, 'unknown')}" for p in analysis.closure_params
        )
        sig = f"export function {fn_name}({params_sig})"
        body = removed_content.rstrip()
        content = f"{import_block}{sig} {{\n{_indent(body, 2)}\n}}\n"
    else:
        body = removed_content.rstrip()
        content = f"{import_block}export function {fn_name}() {{\n{_indent(body, 2)}\n}}\n"

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(content, encoding="utf-8")
    target = file_path or out_path
    import_path = _relative_import_hint(out_path)
    return {
        "module_path": out_path,
        "export_name": fn_name,
        "module_kind": "util",
        "parent_wire": f"import {{ {fn_name} }} from '{import_path}';",
    }


def _relative_import_hint(out_path: str) -> str:
    path = out_path.replace("\\", "/")
    for ext in (".ts", ".tsx"):
        if path.endswith(ext):
            path = path[: -len(ext)]
            break
    return path


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "".join(pad + line if line.strip() else line for line in text.splitlines(keepends=True))
