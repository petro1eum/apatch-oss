#!/usr/bin/env python3
"""Convert apatch strip export (raw else-if bodies) into register_native() module.

Packaged module: run via ``python -m apatch.native_converter`` or import
:func:`main`/:func:`convert` directly. This is intentionally domain-specific to
the O-Lang / Ed Organism C++ interpreter (``register_native``, ``call->callee``,
``Interpreter::Value`` …) but ships inside the wheel so ``apatch strip --to-native``
works on any machine without hardcoded paths.
"""
from __future__ import annotations

import re
import sys
import json
import argparse
from pathlib import Path
from typing import List, Tuple


def replace_keyword_safe(line: str, word: str, replacement: str) -> str:
    """Safe keyword renamer that avoids comments and string literals."""
    # 1. Skip whole-line comments
    if line.strip().startswith("//") or line.strip().startswith("/*") or line.strip().startswith("#"):
        return line

    # 2. If the line has an inline comment, split it
    comment_idx = line.find("//")
    if comment_idx != -1:
        code_part = line[:comment_idx]
        comment_part = line[comment_idx:]
    else:
        code_part = line
        comment_part = ""

    # 3. Split code_part by double quotes to avoid replacing inside string literals
    parts = code_part.split('"')
    for idx in range(len(parts)):
        if idx % 2 == 0:  # Outside string literals
            parts[idx] = re.sub(rf'\b{re.escape(word)}\b', replacement, parts[idx])

    return '"'.join(parts) + comment_part


def transform_body(body_lines: List[str]) -> str:
    out: List[str] = []
    for line in body_lines:
        if "else if (call->callee ==" in line or line.strip().startswith("} else if (call->callee"):
            continue
        l = line
        if l.startswith("        "):
            l = l[8:]

        # 1. First rewrite raw eval(call->args[X]) directly to args[X]
        l = re.sub(r"\beval\(call->args\[(\d+)\]\)", r"args[\1]", l)
        l = re.sub(r"\beval\(args\[(\d+)\]\)", r"args[\1]", l)

        # 2. General call->args conversions
        l = l.replace("call->args.empty()", "args.empty()")
        l = l.replace("call->args.size()", "args.size()")
        l = l.replace("call->args[", "args[")
        l = re.sub(r"\bcall->args\b", "args", l)

        # 3. Automatic removal of interp_. prefix
        l = re.sub(r"\binterp_\.", "", l)
        # C++20: avoid reserved keyword `concept` as identifier safely
        l = replace_keyword_safe(l, "concept", "concept_name")

        # 4. Standard C++ interpreter types resolution
        for a, b in [
            ("Interpreter::Value{}", "Value{}"),
            ("Interpreter::Value", "Value"),
            ("Interpreter::Record", "Record"),
            ("Interpreter::List", "List"),
            ("Interpreter::Lambda", "Lambda"),
            ("list_to_doubles(call->args[", "value_list_to_doubles(*this, args["),
            ("list_to_doubles(args[", "value_list_to_doubles(*this, args["),
        ]:
            l = l.replace(a, b)
        out.append(l)
    return "\n".join(out)


def extract_callees(text: str) -> List[Tuple[str, str]]:
    lines = text.splitlines()
    callees: List[Tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'call->callee == "' in line:
            names = re.findall(r'call->callee == "([^"]+)"', line)
            if not names:
                i += 1
                continue
            body: List[str] = []
            i += 1
            while i < len(lines):
                if 'call->callee == "' in lines[i]:
                    break
                body.append(lines[i])
                i += 1

            # Scan raw body lines for C++ native-decoupling warning/blocker patterns
            for line_idx, body_line in enumerate(body, start=1):
                if "eval(call->args" in body_line or "eval(args" in body_line:
                    for name in names:
                        print(f'CONVERSION_WARNING: {{"callee": "{name}", "kind": "needs_eval_arg", "line": {line_idx}}}', flush=True)
                if "temporal_from_value" in body_line or "get_hist_value" in body_line:
                    for name in names:
                        print(f'CONVERSION_WARNING: {{"callee": "{name}", "kind": "needs_evaluator_context", "line": {line_idx}}}', flush=True)
                if "dynamic_pointer_cast<Identifier>" in body_line:
                    for name in names:
                        print(f'CONVERSION_WARNING: {{"callee": "{name}", "kind": "needs_ast_in_value", "line": {line_idx}}}', flush=True)
                if "measure_time" in body_line:
                    for name in names:
                        print(f'CONVERSION_WARNING: {{"callee": "{name}", "kind": "deferred_eval", "line": {line_idx}}}', flush=True)

            body_t = transform_body(body)
            for name in names:
                callees.append((name, body_t))
        else:
            i += 1
    return callees


SYMBOL_INCLUDES = {
    "OLangTopologyFactory": '#include "olang_stl_topology.hpp"',
    "OLangHyperphasorFactory": '#include "olang_stl_hyperphasor.hpp"',
    "omega_jit::": '#include "backend/jit/jit_bytecode.hpp"',
    "telemetry::": '#include "olang_telemetry.hpp"',
}


def resolve_minimal_includes(body_text: str, parent_includes: List[str]) -> List[str]:
    """
    Intelligently analyzes which custom and standard C++ includes are actually used
    in the body of the native module, resolving the 'minimal include set'.
    """
    selected_includes = []

    # 1. Custom and local C++ include scanning
    for inc in parent_includes:
        # Extract filename (e.g. from '#include "triz_engine.hpp"' -> 'triz_engine')
        match = re.search(r'["<]([^">\s]+)[">]', inc)
        if match:
            inc_file = match.group(1)
            stem = Path(inc_file).stem
            # Split stem by underscore or dash to find constituent keywords
            keywords = re.split(r'[-_]', stem)
            keywords.append(stem)

            matched = False
            for kw in keywords:
                if len(kw) <= 2:
                    continue
                if len(kw) <= 3:
                    pattern = rf'\b{re.escape(kw)}\b'
                else:
                    pattern = re.escape(kw)
                if re.search(pattern, body_text, re.IGNORECASE):
                    matched = True
                    break
            if matched:
                selected_includes.append(inc)

    # 2. Automated standard STL include mapping based on syntax usage
    stl_mapping = {
        "std::shared_ptr": "#include <memory>",
        "std::make_shared": "#include <memory>",
        "std::unique_ptr": "#include <memory>",
        "std::vector": "#include <vector>",
        "std::string": "#include <string>",
        "std::unordered_map": "#include <unordered_map>",
        "std::map": "#include <map>",
        "std::set": "#include <set>",
        "std::unordered_set": "#include <unordered_set>",
        "std::max": "#include <algorithm>",
        "std::min": "#include <algorithm>",
        "std::sort": "#include <algorithm>",
        "std::clamp": "#include <algorithm>",
        "std::find": "#include <algorithm>",
        "std::pow": "#include <cmath>",
        "std::sqrt": "#include <cmath>",
        "std::abs": "#include <cmath>",
        "std::sin": "#include <cmath>",
        "std::cos": "#include <cmath>",
        "std::log": "#include <cmath>",
        "std::cout": "#include <iostream>",
        "std::endl": "#include <iostream>",
        "std::cerr": "#include <iostream>",
        "std::random_device": "#include <random>",
        "std::mt19937": "#include <random>",
    }

    for term, header in stl_mapping.items():
        if term in body_text and header not in selected_includes:
            selected_includes.append(header)

    # 3. Smart symbols lookup dictionary
    for sym, header in SYMBOL_INCLUDES.items():
        if sym in body_text and header not in selected_includes:
            selected_includes.append(header)

    return sorted(list(set(selected_includes)))


def emit_register_cpp(func_name: str, callees: List[Tuple[str, str]], extra_includes: List[str]) -> str:
    needs_list_helper = any("value_list_to_doubles" in b for _, b in callees)

    parts = [
        '#include "olang_parser.hpp"',
        "",
    ]

    # Add all minimal includes
    for inc in extra_includes:
        parts.append(inc)

    parts.append("")
    parts.append("namespace olang {")
    parts.append("")

    if needs_list_helper:
        parts += [
            "namespace {",
            "",
            "std::vector<double> value_list_to_doubles(const Interpreter& interp, const Value& v) {",
            "    std::vector<double> out;",
            "    if (auto lst = std::get_if<std::shared_ptr<List>>(&v)) {",
            "        if (!*lst) return out;",
            "        out.reserve((*lst)->items.size());",
            "        for (const auto& it : (*lst)->items) out.push_back(interp.as_double(it));",
            "    }",
            "    return out;",
            "}",
            "",
            "} // namespace",
            "",
        ]

    parts.append(f"void Interpreter::{func_name}() {{")
    for name, body in callees:
        parts.append(f'    register_native("{name}", [this](const std::vector<Value>& args) -> Value {{')
        parts.append(body)
        parts.append("    });")
        parts.append("")
    parts += ["}", "", "} // namespace olang", ""]
    return "\n".join(parts)


def convert(
    extracted_cpp: Path,
    out_cpp: Path,
    register_func_name: str,
    report: Path | None = None,
    verbose: bool = False,
) -> int:
    """Run the conversion programmatically. Returns a process-style exit code."""
    extracted_cpp = Path(extracted_cpp)
    out_cpp = Path(out_cpp)
    if not extracted_cpp.exists():
        print(f"Error: Source file '{extracted_cpp}' not found.", file=sys.stderr)
        return 1

    src = extracted_cpp.read_text(encoding="utf-8")
    callees = extract_callees(src)

    parent_includes = []
    report = Path(report) if report else None
    if report and report.exists():
        try:
            with open(report, "r", encoding="utf-8") as f_rep:
                report_data = json.load(f_rep)
                parent_includes = report_data.get("parent_imports", [])
                if not parent_includes:
                    parent_includes = report_data.get("all_parent_imports", [])
                if verbose:
                    print(f"Loaded {len(parent_includes)} parent includes from: {report}")
        except Exception as e:
            print(f"Warning: Could not parse report JSON '{report}': {e}", file=sys.stderr)

    combined_body = "\n".join(body for _, body in callees)
    resolved_includes = resolve_minimal_includes(combined_body, parent_includes)

    if "ed_psyche" in src or "EdPsyche" in src:
        has_psyche = any('ed_psyche.hpp' in inc for inc in resolved_includes)
        if not has_psyche:
            resolved_includes.append('#include "ed_psyche.hpp"')

    if verbose:
        print("Resolved C++ Includes:")
        for inc in resolved_includes:
            print(f"  {inc}")

    cpp = emit_register_cpp(register_func_name, callees, resolved_includes)
    out_cpp.write_text(cpp, encoding="utf-8")
    print(f"Wrote {out_cpp.name}: {len(callees)} builtins successfully.")

    if report and report.exists():
        try:
            with open(report, "r+", encoding="utf-8") as f_rep:
                report_data = json.load(f_rep)
                report_data["resolved_includes"] = resolved_includes
                report_data["include_resolution"] = "symbol_table+stem+fallback"
                f_rep.seek(0)
                json.dump(report_data, f_rep, indent=2, ensure_ascii=False)
                f_rep.truncate()
        except Exception as e:
            print(f"Warning: Could not update report JSON '{report}': {e}", file=sys.stderr)

    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert raw else-if C++ bodies into a clean native register_native() module.")
    parser.add_argument("extracted_cpp", type=Path, help="Path to extracted raw else-if block C++ file.")
    parser.add_argument("out_cpp", type=Path, help="Output destination for compiled register C++ file.")
    parser.add_argument("register_func_name", type=str, help="Name of the native interpreter registration method.")
    parser.add_argument("--report", type=Path, help="Optional path to extraction_report.json to automatically resolve custom parent includes.")
    parser.add_argument("--verbose", action="store_true", help="Print detailed transformation feedback.")
    args = parser.parse_args(argv)

    return convert(
        extracted_cpp=args.extracted_cpp,
        out_cpp=args.out_cpp,
        register_func_name=args.register_func_name,
        report=args.report,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
