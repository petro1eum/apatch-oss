"""Resolve parent imports for strip (regex + tree-sitter TS + tsconfig paths)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from apatch.apatch_paths import normalize_rel


def resolve_parent_imports(file_path: str, content: str, workspace: Optional[str] = None) -> List[str]:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".ts", ".tsx", ".jsx", ".js"):
        ts_imports = _ts_tree_sitter_imports(content)
        if ts_imports:
            return ts_imports
    return _regex_parent_imports(file_path, content)


def _regex_parent_imports(file_path: str, content: str) -> List[str]:
    ext = os.path.splitext(file_path)[1].lower()
    imports: List[str] = []
    if ext == ".py":
        # Real import grammar only — a docstring/prose line beginning with
        # "from "/"import " must NOT be mistaken for a module import
        # (a from-import requires the `import` keyword; RFP-027 dogfood finding).
        imports = re.findall(
            r"^[ \t]*(?:from[ \t]+[\w.]+[ \t]+import[ \t]+.+|import[ \t]+[\w.]+.*)",
            content,
            re.MULTILINE,
        )
    elif ext in (".cpp", ".hpp", ".h", ".omega"):
        imports = re.findall(r"^\s*#include\s+.+", content, re.MULTILINE)
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        imports = re.findall(
            r"^\s*(?:import\s+.+from\s+['\"].+['\"]|import\s+['\"].+['\"])",
            content,
            re.MULTILINE,
        )
        imports.extend(
            re.findall(r"^\s*(?:const|let|var)\s+.+=\s+require\(.+", content, re.MULTILINE)
        )
        imports.extend(re.findall(r"^\s*import\s+type\s+.+", content, re.MULTILINE))
    elif ext == ".rs":
        imports = re.findall(r"^\s*(?:use|extern\s+crate)\s+.+", content, re.MULTILINE)
    elif ext in (".go", ".java", ".kt"):
        imports = re.findall(r"^\s*import\s+.+", content, re.MULTILINE)
    else:
        imports = re.findall(
            r"^\s*(?:import|using|require|require_relative|include|use|source)\s+.+",
            content,
            re.MULTILINE,
        )
    return list(sorted(set(line.strip() for line in imports if line.strip())))


def _ts_tree_sitter_imports(content: str) -> List[str]:
    try:
        from apatch.matcher import load_language
        from tree_sitter import Parser

        lang = load_language("typescript")
        if lang is None:
            return []
        parser = Parser(lang)
        tree = parser.parse(content.encode("utf-8"))
        lines: List[str] = []
        for node in _walk(tree.root_node):
            if node.type == "import_statement":
                seg = content[node.start_byte : node.end_byte]
                line = seg.split("\n", 1)[0].strip()
                if line:
                    lines.append(line)
        return list(sorted(set(lines)))
    except Exception:
        return []


def _walk(node):
    yield node
    for i in range(node.child_count):
        yield from _walk(node.child(i))


def read_tsconfig_paths(workspace: str) -> Dict[str, List[str]]:
    """Read compilerOptions.paths from nearest tsconfig.json."""
    start = Path(workspace).resolve()
    for directory in [start, *start.parents]:
        tsconfig = directory / "tsconfig.json"
        if tsconfig.is_file():
            try:
                data = json.loads(tsconfig.read_text(encoding="utf-8"))
                return data.get("compilerOptions", {}).get("paths", {}) or {}
            except Exception:
                return {}
    return {}


def read_tsconfig_base_url(workspace: str) -> str:
    start = Path(workspace).resolve()
    for directory in [start, *start.parents]:
        tsconfig = directory / "tsconfig.json"
        if tsconfig.is_file():
            try:
                data = json.loads(tsconfig.read_text(encoding="utf-8"))
                return data.get("compilerOptions", {}).get("baseUrl", ".") or "."
            except Exception:
                return "."
    return "."


def resolve_path_alias(module_spec: str, file_path: str) -> str:
    """Resolve @/ alias to a project-relative path using tsconfig paths."""
    workspace = str(Path(file_path).resolve().parent)
    paths = read_tsconfig_paths(workspace)
    base_url = read_tsconfig_base_url(workspace)
    for alias, targets in paths.items():
        alias_pat = alias.replace("*", r"(.*)")
        m = re.match(f"^{alias_pat}$", module_spec)
        if not m:
            continue
        captured = m.group(1) if m.lastindex else ""
        target = targets[0].replace("*", captured)
        resolved = os.path.normpath(os.path.join(base_url, target)).replace("\\", "/")
        return resolved
    return module_spec


def _parse_named_import_list(module: str, names_blob: str) -> List[Tuple[str, str]]:
    results: List[Tuple[str, str]] = []
    for part in names_blob.split(","):
        part = part.strip()
        if not part:
            continue
        if " as " in part:
            _orig, local = part.split(" as ", 1)
            results.append((local.strip(), module))
        else:
            results.append((part.strip(), module))
    return results


def parse_import_bindings(import_line: str) -> List[Tuple[str, str]]:
    """Return (local_name, module_spec) pairs from an import line."""
    results: List[Tuple[str, str]] = []
    stripped = import_line.strip()
    combined = re.match(
        r"""import\s+(?:type\s+)?(\w+)\s*,\s*\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]\s*;?""",
        stripped,
    )
    if combined:
        module = combined.group(3)
        results.append((combined.group(1), module))
        results.extend(_parse_named_import_list(module, combined.group(2)))
        return results
    m = re.match(
        r"""import\s+(?:type\s+)?(?:\{([^}]+)\}|(\w+))\s+from\s+['"]([^'"]+)['"]\s*;?""",
        stripped,
    )
    if not m:
        m2 = re.match(r"""import\s+['"]([^'"]+)['"]""", import_line.strip())
        if m2:
            return []
        return results
    module = m.group(3)
    if m.group(2):
        results.append((m.group(2), module))
    elif m.group(1):
        results.extend(_parse_named_import_list(module, m.group(1)))
    return results


def build_symbol_import_map(
    parent_content: str,
    file_path: str,
) -> Dict[str, str]:
    """Map local symbol names to resolved module paths."""
    imports = resolve_parent_imports(file_path, parent_content)
    sym_map: Dict[str, str] = {}
    for imp in imports:
        for local, module in parse_import_bindings(imp):
            resolved = resolve_path_alias(module, file_path)
            sym_map[local] = resolved
    return sym_map


def locate_ts_module_file(consumer_root: str, module_rel: str) -> Optional[str]:
    """Resolve a project-relative module path to an on-disk TS/JS file."""
    rel = normalize_rel(module_rel)
    base = Path(consumer_root) / rel
    candidates = [
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base.with_suffix(".js"),
        base.with_suffix(".jsx"),
        base / "index.ts",
        base / "index.tsx",
        base / "index.js",
        base / "index.jsx",
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


def _split_top_level_commas(fragment: str) -> List[str]:
    parts: List[str] = []
    depth = 0
    angle = 0
    cur: List[str] = []
    for ch in fragment:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "<":
            angle += 1
        elif ch == ">":
            angle -= 1
        elif ch == "," and depth == 0 and angle == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_balanced_segment(source: str, open_idx: int, open_ch: str, close_ch: str) -> Optional[str]:
    if open_idx >= len(source) or source[open_idx] != open_ch:
        return None
    depth = 0
    angle = 0
    for i in range(open_idx, len(source)):
        ch = source[i]
        if ch == "<":
            angle += 1
        elif ch == ">":
            angle -= 1
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return source[open_idx + 1 : i]
    return None


def _format_fn_params(params_inner: str) -> str:
    inner = params_inner.strip()
    if not inner:
        return ""
    typed: List[str] = []
    for part in _split_top_level_commas(inner):
        token = part.strip()
        if not token:
            continue
        if token.startswith("..."):
            typed.append(token)
            continue
        m = re.match(r"([a-zA-Z_$][\w$]*)\s*:\s*(.+)", token, re.DOTALL)
        if m:
            type_part = re.split(r"\s*=", m.group(2).strip(), maxsplit=1)[0].strip()
            typed.append(f"{m.group(1)}: {type_part}")
        else:
            typed.append(f"{token}: unknown")
    return ", ".join(typed)


def _infer_async_return_type(body_slice: str) -> str:
    gm = re.search(r"safeFetch<([^>]+)>", body_slice)
    if gm:
        return f"Promise<{gm.group(1).strip()}>"
    pm = re.search(r":\s*Promise<([^>]+)>", body_slice)
    if pm:
        return f"Promise<{pm.group(1).strip()}>"
    return "Promise<unknown>"


def _function_type_from_signature(
    content: str,
    name: str,
    *,
    is_async: bool,
    params_inner: str,
    close_paren_idx: int,
) -> str:
    param_str = _format_fn_params(params_inner)
    after = content[close_paren_idx + 1 :].lstrip()
    ret: Optional[str] = None
    if after.startswith(":"):
        ret_candidate = after[1:].lstrip()
        if ret_candidate.startswith("Promise<"):
            generic = _extract_balanced_segment(ret_candidate, 8, "<", ">")
            if generic is not None:
                ret = f"Promise<{generic.strip()}>"
        else:
            m = re.match(r"([^\s{;]+)", ret_candidate)
            if m:
                ret = m.group(1).strip()
    if not ret:
        body_start = content.find("{", close_paren_idx)
        slice_end = (body_start + 480) if body_start >= 0 else close_paren_idx + 480
        if is_async:
            ret = _infer_async_return_type(content[close_paren_idx:slice_end])
        else:
            ret = "void"
    if not param_str:
        return f"() => {ret}"
    return f"({param_str}) => {ret}"


def _parse_exported_function_type(content: str, name: str) -> Optional[str]:
    prefixes = (
        ("export async function ", True),
        ("export function ", False),
        ("async function ", True),
        ("function ", False),
    )
    for prefix, is_async in prefixes:
        needle = f"{prefix}{name}"
        idx = content.find(needle)
        if idx < 0:
            continue
        paren_start = content.find("(", idx + len(needle))
        if paren_start < 0:
            continue
        params_inner = _extract_balanced_segment(content, paren_start, "(", ")")
        if params_inner is None:
            continue
        close_paren_idx = paren_start + len(params_inner) + 1
        return _function_type_from_signature(
            content,
            name,
            is_async=is_async,
            params_inner=params_inner,
            close_paren_idx=close_paren_idx,
        )
    return None


def _parse_exported_const_type(content: str, name: str) -> Optional[str]:
    typed = re.search(
        rf"export\s+const\s+{re.escape(name)}\s*:\s*([^=;\n]+)",
        content,
    )
    if typed:
        return typed.group(1).strip()
    arrow = re.search(
        rf"export\s+const\s+{re.escape(name)}\s*=\s*(async\s*)?\(([^)]*)\)\s*=>",
        content,
        re.DOTALL,
    )
    if arrow:
        is_async = bool(arrow.group(1))
        params_inner = arrow.group(2) or ""
        param_str = _format_fn_params(params_inner)
        ret = "Promise<unknown>" if is_async else "void"
        if not param_str:
            return f"() => {ret}"
        return f"({param_str}) => {ret}"
    return None


def _parse_exported_type_alias(module_content: str, symbol: str) -> Optional[str]:
    m = re.search(rf"export\s+type\s+{re.escape(symbol)}\s*=\s*", module_content)
    if not m:
        return None
    start = m.end()
    if start < len(module_content) and module_content[start] == "{":
        inner = _extract_balanced_segment(module_content, start, "{", "}")
        if inner is not None:
            return "{" + inner + "}"
    line = re.match(r"([^;\n]+)", module_content[start:])
    return line.group(1).strip() if line else None


def parse_exported_symbol_type(module_content: str, symbol: str) -> Optional[str]:
    """Best-effort TS export type for a symbol (function, const, type, interface)."""
    type_alias = _parse_exported_type_alias(module_content, symbol)
    if type_alias:
        return type_alias
    if re.search(rf"export\s+interface\s+{re.escape(symbol)}\b", module_content):
        return symbol
    fn_type = _parse_exported_function_type(module_content, symbol)
    if fn_type:
        return fn_type
    const_type = _parse_exported_const_type(module_content, symbol)
    if const_type:
        return const_type
    return None


def lookup_imported_symbol_type(
    symbol: str,
    module_rel: str,
    file_path: str,
) -> Optional[str]:
    """Resolve a closure param type from its import source module."""
    from apatch.import_paths import resolve_consumer_root

    consumer_root = resolve_consumer_root(file_path)
    module_path = locate_ts_module_file(consumer_root, module_rel)
    if not module_path:
        return None
    try:
        module_content = Path(module_path).read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_exported_symbol_type(module_content, symbol)


def _destructure_binding_names(fragment: str) -> List[str]:
    names: List[str] = []
    for part in fragment.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" in token:
            token = token.split(":", 1)[0].strip()
        names.append(token)
    return names


def _extract_angle_generic(source: str, start: int) -> Optional[str]:
    """Return inner text of a balanced <...> generic starting at '<'."""
    if start >= len(source) or source[start] != "<":
        return None
    depth = 0
    for i in range(start, len(source)):
        ch = source[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0:
                return source[start + 1 : i]
    return None


def build_symbol_type_map(parent_content: str) -> Dict[str, str]:
    """Heuristic: extract TypeScript types from parent declarations."""
    types: Dict[str, str] = {}
    # const [x, setX] = useState<Type>() or useState(initial)
    for m in re.finditer(
        r"\bconst\s+\[\s*([a-zA-Z_$][\w$]*)\s*,\s*([a-zA-Z_$][\w$]*)\s*\]\s*=\s*useState",
        parent_content,
    ):
        state_var, setter = m.group(1), m.group(2)
        generic = _extract_angle_generic(parent_content, m.end())
        if generic:
            types[state_var] = generic.strip()
            types[setter] = f"React.Dispatch<React.SetStateAction<{generic.strip()}>>"
            continue
        init_m = re.match(
            r"\s*\(\s*(['\"][^'\"]*['\"]|\d+|true|false|null)\s*\)",
            parent_content[m.end() : m.end() + 48],
        )
        if init_m:
            init = init_m.group(1)
            if init.startswith(("'", '"')):
                types[state_var] = "string"
            elif init in ("true", "false"):
                types[state_var] = "boolean"
            elif init == "null":
                types[state_var] = "unknown | null"
            elif init.isdigit():
                types[state_var] = "number"
            else:
                types[state_var] = "unknown"
            types[setter] = f"React.Dispatch<React.SetStateAction<{types[state_var]}>>"
        else:
            types[setter] = "React.Dispatch<React.SetStateAction<unknown>>"
    # const fooRef = useRef<Type>(initial)
    for m in re.finditer(
        r"\bconst\s+([a-zA-Z_$][\w$]*)\s*=\s*useRef\s*(?:<([^>]+)>)?\s*\(\s*([^)]*)\)",
        parent_content,
    ):
        ref_name = m.group(1)
        generic = (m.group(2) or "").strip()
        init = (m.group(3) or "").strip()
        if generic:
            types[ref_name] = f"React.MutableRefObject<{generic}>"
        elif init in ("false", "true"):
            types[ref_name] = "React.MutableRefObject<boolean>"
        elif init in ("null", "undefined") or (
            init.startswith(("'", '"')) and ("Id" in ref_name or "id" in ref_name)
        ):
            types[ref_name] = "React.MutableRefObject<string | null>"
        elif init.startswith(("'", '"')):
            types[ref_name] = "React.MutableRefObject<string>"
        else:
            types[ref_name] = "React.MutableRefObject<unknown>"
    # const { loadAll, activeProjectId } = useFooStore()
    for m in re.finditer(
        r"\bconst\s+\{\s*([^}]+)\s*\}\s*=\s*(\w+)\s*\(\s*\)",
        parent_content,
    ):
        for name in _destructure_binding_names(m.group(1)):
            if re.match(r"load\w+", name) or name.startswith("patch"):
                types[name] = "() => Promise<void>"
            elif name == "activeProjectId":
                types[name] = "string | null"
    # const foo: Type = ...
    for m in re.finditer(
        r"\b(?:const|let)\s+([a-zA-Z_$][\w$]*)\s*:\s*([^=;]+)\s*=",
        parent_content,
    ):
        types[m.group(1)] = m.group(2).strip()
    # const handler = (arg: Type, ...) => ...
    for m in re.finditer(
        r"\bconst\s+([a-zA-Z_$][\w$]*)\s*=\s*\(([^)]*)\)\s*=>",
        parent_content,
    ):
        params = m.group(2)
        param_types: List[str] = []
        for pm in re.finditer(r"([a-zA-Z_$][\w$]*)\s*:\s*([^,)]+)", params):
            param_types.append(f"{pm.group(1)}: {pm.group(2).strip()}")
        if param_types:
            types[m.group(1)] = f"({', '.join(param_types)}) => void"
    # function foo(arg: Type)
    for m in re.finditer(
        r"\bfunction\s+([a-zA-Z_$][\w$]*)\s*\(([^)]*)\)",
        parent_content,
    ):
        params = m.group(2)
        for pm in re.finditer(r"([a-zA-Z_$][\w$]*)\s*:\s*([^,)]+)", params):
            types[pm.group(1)] = pm.group(2).strip()
    # useFoo() return — Dispatch<SetStateAction<...>> for setters
    for m in re.finditer(r"\b(set[A-Z][a-zA-Z0-9_]*)\s*=", parent_content):
        setter = m.group(1)
        if setter not in types:
            types[setter] = "React.Dispatch<React.SetStateAction<unknown>>"
    return types


def infer_param_types(
    params: List[str],
    parent_content: str,
    file_path: str,
) -> Dict[str, str]:
    type_map = build_symbol_type_map(parent_content)
    import_map = build_symbol_import_map(parent_content, file_path)
    out: Dict[str, str] = {}
    for p in params:
        if (
            p == "sheet"
            and "GreenSheet" in parent_content
            and re.search(r"if\s*\([^)]*!sheet", parent_content)
        ):
            out[p] = "GreenSheet"
            continue
        if p in type_map:
            out[p] = type_map[p]
        elif p in import_map:
            imported_type = lookup_imported_symbol_type(p, import_map[p], file_path)
            if imported_type:
                out[p] = imported_type
            else:
                out[p] = "unknown /* from " + import_map[p] + " */"
        elif (p.endswith("Stats") or p == "monthStats") and (
            "PeriodMetrics" in parent_content or "quotaAnalytics" in parent_content
        ):
            out[p] = "PeriodMetrics[]"
        elif p == "dispatch":
            out[p] = "AppDispatch"
        elif p == "navigate":
            out[p] = "NavigateFunction"
        elif p == "can":
            out[p] = "(permission: Permission) => boolean"
        elif p.startswith("on") and p.endswith("Change"):
            out[p] = "(value: string) => void"
        elif p.startswith("on") and "Filter" in p:
            out[p] = "(value: string) => void"
        elif p == "onActionStatusToggle":
            out[p] = "(id: string, checked: boolean) => void"
        elif p == "onStageSliderChange":
            out[p] = "(dealId: string, newStageIndex: number) => void"
        elif p == "loadingLatest":
            out[p] = "boolean"
        elif p == "latestLink":
            out[p] = "MailMessageLink | null"
        elif p == "toggleRow":
            out[p] = "(key: string) => void"
        elif p == "titlePrefix":
            out[p] = "string"
        elif p == "year":
            out[p] = "number"
        elif p.endswith("Ref"):
            out[p] = type_map.get(p, "React.MutableRefObject<unknown>")
        elif re.match(r"load\w+", p) or p.startswith("patch"):
            out[p] = "() => Promise<void>"
        elif p == "activeProjectId":
            out[p] = type_map.get(p, "string | null")
        elif p == "mode":
            out[p] = type_map.get(p, "string")
        elif p.startswith("set") and p[3:4].isupper():
            out[p] = type_map.get(p, "React.Dispatch<React.SetStateAction<unknown>>")
        elif p == "onEditPeriod":
            out[p] = "(row: { quarter?: string; month?: number }) => void"
        elif p == "setSubTab":
            out[p] = "React.Dispatch<React.SetStateAction<'quarters' | 'months'>>"
        elif p in ("setView", "setSelectedRep"):
            out[p] = "React.Dispatch<React.SetStateAction<any>>"
        elif p == "onSelectRep" or p == "setSelectedRep":
            out[p] = "React.Dispatch<React.SetStateAction<string | null>>"
        elif p == "repNames":
            out[p] = "string[]"
        elif p == "defaultRep":
            out[p] = "string | undefined"
        elif p == "isDirector":
            out[p] = "boolean"
        elif p.startswith("handle") or p.startswith("on"):
            out[p] = "() => void"
        elif p == "sheet" and "GreenSheet" in parent_content:
            out[p] = "GreenSheet"
        elif p == "deal" and (
            "ClientDeal" in parent_content or "deals.find" in parent_content or "revenueSoftware" in parent_content
        ):
            out[p] = "ClientDeal"
        elif p == "client" and "Client" in parent_content:
            out[p] = "Client | null | undefined"
        elif p == "dealContacts" and "dealContexts" in parent_content:
            out[p] = "DealContactContext[]"
        elif p == "allContacts" and "contacts.contacts" in parent_content:
            out[p] = "Contact[]"
        elif p == "updateField" and "GreenSheet" in parent_content:
            out[p] = "(field: keyof GreenSheet, value: unknown) => void"
        elif p == "prepProgress":
            out[p] = "number"
        elif p.startswith("check"):
            out[p] = "boolean"
        else:
            out[p] = "unknown"
    return out


def _narrow_import_line(imp: str, used_locals: Set[str]) -> Optional[str]:
    """Rebuild a named import with only symbols referenced in the extracted block."""
    bindings = parse_import_bindings(imp)
    if not bindings:
        return imp
    picked = [local for local, _mod in bindings if local in used_locals]
    if not picked:
        return None
    if len(picked) == len(bindings):
        return imp
    module = bindings[0][1]
    if picked == ["React"] and module.rstrip("/").endswith("react"):
        return "import React from 'react';"
    prefix = "import type" if "import type" in imp else "import"
    names = ", ".join(picked)
    return f"{prefix} {{ {names} }} from '{module}';"


def collect_import_statements(parent_content: str) -> List[str]:
    """Extract import lines/blocks from parent source (including multiline named imports)."""
    statements: List[str] = []
    for match in re.finditer(
        r"import\s[\s\S]*?from\s+['\"][^'\"]+['\"]\s*;?",
        parent_content,
    ):
        stmt = " ".join(match.group(0).split())
        if stmt not in statements:
            statements.append(stmt)
    return statements


def filter_imports_for_content(
    parent_imports: List[str],
    removed_content: str,
    parent_content: str,
    file_path: str,
) -> List[str]:
    """Keep imports whose symbols appear in the removed block."""
    needed: Set[str] = set(re.findall(r"\b([A-Za-z_$][\w$]*)\b", removed_content))
    sym_to_import: Dict[str, str] = {}
    merged_imports = list(parent_imports)
    if parent_content:
        for stmt in collect_import_statements(parent_content):
            if stmt not in merged_imports:
                merged_imports.append(stmt)
    for imp in merged_imports:
        for local, _mod in parse_import_bindings(imp):
            sym_to_import[local] = imp

    out: List[str] = []
    seen: Set[str] = set()
    for sym, imp in sym_to_import.items():
        if sym in needed and sym not in {"Plan"}:
            narrowed = _narrow_import_line(imp, needed) or imp
            if narrowed not in seen:
                out.append(narrowed)
                seen.add(narrowed)

    # Also match bare module stems in content (e.g. types from @/ paths)
    if not out:
        for imp in parent_imports:
            for local, module in parse_import_bindings(imp):
                stem = os.path.basename(resolve_path_alias(module, file_path)).split(".")[0]
                if stem and stem in removed_content:
                    out.append(imp)

    if not out and parent_imports:
        # Fallback: keep only type-only and react imports; never dump unrelated parent imports.
        for imp in parent_imports:
            if "import type" in imp or "react" in imp.lower():
                out.append(imp)
    return out


def _iter_import_blocks(content: str) -> List[Tuple[str, int, int]]:
    blocks: List[Tuple[str, int, int]] = []
    for match in re.finditer(
        r"import\s[\s\S]*?from\s+['\"][^'\"]+['\"]\s*;?",
        content,
    ):
        blocks.append((match.group(0), match.start(), match.end()))
    return blocks


def _body_for_import_usage_scan(content: str) -> str:
    """Code body with imports, comments, and string literals stripped for usage checks."""
    out = content
    for _block, start, end in reversed(_iter_import_blocks(content)):
        out = out[:start] + (" " * (end - start)) + out[end:]
    out = re.sub(r"/\*.*?\*/", "", out, flags=re.DOTALL)
    out = re.sub(r"//[^\n]*", "", out)
    out = re.sub(r"'([^'\\]|\\.)*'", "''", out)
    out = re.sub(r'"([^"\\]|\\.)*"', '""', out)
    out = re.sub(r"`([^`\\]|\\.)*`", "``", out)
    return out


def _import_bindings_from_block(block_text: str) -> List[str]:
    stmt = " ".join(block_text.split())
    bindings = [local for local, _mod in parse_import_bindings(stmt)]
    if bindings:
        return bindings
    from_match = re.search(r"""from\s+['"]([^'"]+)['"]""", stmt)
    if not from_match:
        return []
    names_blob = re.search(r"\{([^}]+)\}", stmt)
    if names_blob:
        return [local for local, _ in _parse_named_import_list(from_match.group(1), names_blob.group(1))]
    default = re.search(r"""import\s+(?:type\s+)?(\w+)\s+from""", stmt)
    if default:
        return [default.group(1)]
    return []


def prune_unused_imports(content: str) -> str:
    """Remove or narrow import statements whose bindings are unused in the file body."""
    blocks = _iter_import_blocks(content)
    if not blocks:
        return content
    out = content
    for block_text, start, end in reversed(blocks):
        usage_body = _body_for_import_usage_scan(out)
        bindings = _import_bindings_from_block(block_text)
        if not bindings:
            continue
        stmt = " ".join(block_text.split())
        used = [sym for sym in bindings if re.search(rf"\b{re.escape(sym)}\b", usage_body)]
        if len(used) == len(bindings):
            continue
        if not used:
            out = out[:start] + out[end:]
        else:
            narrowed = _narrow_import_line(stmt, set(used))
            if narrowed:
                out = out[:start] + narrowed + "\n" + out[end:]
        out = re.sub(r"\n{3,}", "\n\n", out)
    return out


# Backward-compatible alias
def filter_imports(parent_imports: List[str], removed_content: str) -> List[str]:
    return filter_imports_for_content(parent_imports, removed_content, "", "")
