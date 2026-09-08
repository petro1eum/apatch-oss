"""Language-specific wiring_hints and integration_hints templates."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from apatch.import_paths import workspace_ts_import
from apatch.strip import StripSpec


def detect_profile(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".cpp", ".hpp", ".h", ".cc", ".cxx", ".omega"):
        return "cpp"
    if ext in (".ts", ".tsx", ".jsx", ".js"):
        return "typescript"
    if ext == ".py":
        return "python"
    return "generic"


def build_cpp_wiring_hints(
    *,
    filename: str,
    reg_func: str,
    native_out_dir: Optional[str],
    out_dir: Optional[str],
) -> Dict[str, str]:
    cmake_snippet = (
        f"src/frontend/interpreter/{filename}"
        if native_out_dir and "interpreter" in native_out_dir
        else f"{native_out_dir or out_dir or '.'}/{filename}"
    )
    cmake_snippet = os.path.normpath(cmake_snippet)
    cmake_embedded = (
        f"${{OLANG_CPP_DIR}}/{cmake_snippet}"
        if not os.path.isabs(cmake_snippet)
        else cmake_snippet
    )
    return {
        "register_func": reg_func,
        "header_decl": f"void {reg_func}();",
        "registration_call": f"{reg_func}();",
        "cmake_snippet": cmake_snippet,
        "cmake_embedded_snippet": cmake_embedded,
    }


def _pascal_label(label: str) -> str:
    import re as _re

    parts = _re.split(r"[_\-\s]+", label or "extracted")
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Extracted"


def _resolve_hook_name(label: str, target: str, spec: StripSpec) -> str:
    from apatch.converters.ts_module import resolve_hook_export_name

    return resolve_hook_export_name(
        label=label,
        target_module=target,
        manifest_replace=spec.replace,
        parent_wire=spec.parent_wire,
    )


def _resolve_component_name(label: str, target: str, spec: StripSpec) -> str:
    from apatch.converters.ts_module import resolve_component_export_name

    return resolve_component_export_name(
        label=label,
        target_module=target,
        manifest_replace=spec.replace,
    )


def build_typescript_wiring_hints(
    *,
    spec: StripSpec,
    filename: str,
    module_out_path: str,
    label: str,
    to_module: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, str]:
    import re as _re

    module_kind = (to_module or spec.module_kind or "hook").lower()
    parts = _re.split(r"[_\-\s]+", label or "extracted")
    target = spec.target_module or module_out_path
    hook_name = _resolve_hook_name(label, target, spec)
    comp_name = _resolve_component_name(label, target, spec)
    util_name = parts[0].lower() if parts and parts[0] else "extractedUtil"
    if workspace and target and comp_name:
        import_line_default = workspace_ts_import(target, workspace, comp_name)
    else:
        import_path = _ts_import_path(target)
        import_line_default = f"import {{ {comp_name} }} from '{import_path}';"

    if module_kind == "component":
        import_line = spec.parent_import or import_line_default
        from apatch.auto_wire import extract_jsx_wire_from_replace

        wire = (
            spec.parent_wire
            or extract_jsx_wire_from_replace(spec.replace, comp_name)
            or f"<{comp_name} />"
        )
        barrel = f"export {{ {comp_name} }} from './{_basename_no_ext(target)}';"
        export_symbol = comp_name
    elif module_kind == "util":
        import_path = _ts_import_path(target)
        import_line = spec.parent_import or f"import {{ {util_name} }} from '{import_path}';"
        wire = spec.parent_wire or f"{util_name}(/* args */)"
        barrel = f"export {{ {util_name} }} from './{_basename_no_ext(target)}';"
        export_symbol = util_name
    else:
        import_path = _ts_import_path(target)
        if workspace and target:
            import_line = spec.parent_import or workspace_ts_import(target, workspace, hook_name)
        else:
            import_line = spec.parent_import or f"import {{ {hook_name} }} from '{import_path}';"
        wire = spec.parent_wire or f"const handlers = {hook_name}({{ /* pass deps */ }});"
        barrel = f"export {{ {hook_name} }} from './{_basename_no_ext(target)}';"
        export_symbol = hook_name
    route_redirect = ""
    if spec.route_from and spec.route_to:
        route_redirect = f"{spec.route_from} → {spec.route_to}"
    elif spec.route_from or spec.route_to:
        route_redirect = spec.route_to or spec.route_from
    return {
        "import_line": import_line,
        "parent_wire": wire,
        "barrel_export": barrel,
        "route_redirect": route_redirect,
        "verify_command": spec.verify_command or "npm run build",
        "target_module": target,
        "module_kind": module_kind,
        "export_symbol": export_symbol,
    }


def build_integration_hints(
    *,
    profile: str,
    spec: StripSpec,
    out_dir: Optional[str],
    verify_command: Optional[str],
    export_paths: List[str],
) -> Dict[str, Any]:
    exclude = list(spec.exclude_from_compile or [])
    if out_dir and profile == "typescript":
        exclude.append(os.path.join(out_dir, "*.fragment.txt"))
        exclude.append(os.path.join(out_dir, "*.fragment.tsx"))
    checklist = _post_strip_checklist(profile, spec)
    return {
        "target_module": spec.target_module,
        "module_kind": spec.module_kind,
        "parent_import": spec.parent_import,
        "parent_wire": spec.parent_wire,
        "exclude_from_compile": exclude,
        "verify_command": spec.verify_command or verify_command or _default_verify(profile),
        "post_strip_checklist": checklist,
        "export_paths": export_paths,
        "route_from": spec.route_from,
        "route_to": spec.route_to,
    }


def emit_wiring_markdown(
    *,
    profile: str,
    source_file: str,
    exported_meta: List[Dict[str, Any]],
) -> str:
    lines: List[str] = []
    if profile == "typescript":
        lines.append("# Apatch TypeScript/React Integration Wiring\n")
        lines.append(f"Extracted from `{os.path.basename(source_file)}`.\n")
        for meta in exported_meta:
            hints = meta.get("wiring_hints", {})
            ih = meta.get("integration_hints", {})
            lines.append(f"## Block: `{meta.get('label', '')}`\n")
            lines.append("### 1. Import in parent")
            lines.append(f"```typescript\n{hints.get('import_line', '')}\n```\n")
            lines.append("### 2. Wire in component")
            lines.append(f"```typescript\n{hints.get('parent_wire', '')}\n```\n")
            if hints.get("barrel_export"):
                lines.append("### 3. Barrel export (optional)")
                lines.append(f"```typescript\n{hints['barrel_export']}\n```\n")
            if hints.get("route_redirect"):
                lines.append("### 4. Route redirect (if applicable)")
                lines.append(f"Update router: `{hints['route_redirect']}`\n")
            lines.append(f"### Verify\n```bash\n{ih.get('verify_command', 'npm run build')}\n```\n")
            lines.append("---\n")
        return "\n".join(lines)

    lines.append("# Apatch Native Integration Wiring Hints\n")
    lines.append(f"Wire extracted native modules from `{os.path.basename(source_file)}`.\n")
    for meta in exported_meta:
        hints = meta.get("wiring_hints", {})
        lines.append(f"## Module: `{meta.get('filename', '')}`\n")
        lines.append("### 1. CMakeLists.txt Integration")
        lines.append(f"```cmake\n{hints.get('cmake_embedded_snippet', '')}\n```\n")
        lines.append("### 2. Header declaration")
        lines.append(f"```cpp\n{hints.get('header_decl', '')}\n```\n")
        lines.append("### 3. Registration call")
        lines.append(f"```cpp\n{hints.get('registration_call', '')}\n```\n")
        lines.append("---\n")
    return "\n".join(lines)


def _ts_import_path(target_module: str) -> str:
    path = target_module.replace("\\", "/")
    if path.endswith(".ts"):
        path = path[:-3]
    if path.endswith(".tsx"):
        path = path[:-4]
    return path


def _basename_no_ext(path: str) -> str:
    base = os.path.basename(path)
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _default_verify(profile: str) -> str:
    if profile == "typescript":
        return "npm run build"
    if profile == "cpp":
        return "cmake --build build"
    if profile == "python":
        return "pytest"
    return ""


def _post_strip_checklist(profile: str, spec: StripSpec) -> List[str]:
    base = ["Remove stub comment after wiring", "Run project verify command"]
    if profile == "typescript":
        base.insert(1, "Delete raw .fragment.txt files from extracted/ after module is wired")
        if spec.module_kind == "hook":
            base.insert(1, "Connect hook return values in parent JSX/handlers")
    if profile == "cpp":
        base.insert(1, "Add native .cpp to CMakeLists.txt")
    return base
