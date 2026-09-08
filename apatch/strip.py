"""Marker-based block removal/replacement in source files (refactor stubs)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Union


@dataclass
class StripSpec:
    start: str
    until: str  # exclusive end line; alias in manifests: end_before
    replace: str = ""
    label: str = ""
    export: str = ""
    register: str = ""
    target_module: str = ""
    module_kind: str = ""
    parent_import: str = ""
    parent_wire: str = ""
    verify_command: str = ""
    exclude_from_compile: List[str] = field(default_factory=list)
    route_from: str = ""
    route_to: str = ""


def default_export_ext(file_path: Union[str, Path]) -> str:
    """Language-aware default export extension (R7: TS fragments avoid tsc)."""
    ext = Path(file_path).suffix.lower()
    if ext in (".ts", ".tsx", ".jsx"):
        return ".fragment.txt"
    if ext in (".js",):
        return ".fragment.txt"
    if ext in (".py",):
        return ".fragment.txt"
    if ext in (".cpp", ".hpp", ".h", ".cc", ".cxx", ".omega"):
        return ".extracted.cpp"
    return ".fragment.txt"


def _is_hook_until_closer(line: str) -> bool:
    """True when a line is a useEffect/useCallback closer left after hook replace."""
    stripped = line.strip()
    if re.fullmatch(r"\},?\s*\[[^\]]*\]\s*\);?", stripped):
        return True
    if re.search(r"\},\s*\[", stripped):
        return True
    return stripped.endswith("});")


@dataclass
class StripResult:
    spec: StripSpec
    start_line: int
    end_line: int
    removed_lines: int
    ok: bool
    error: str = ""
    removed_content: str = ""
    boundary_warnings: List[str] = field(default_factory=list)


# Matches `} else if (...)` bridge lines in C++ if-else chains.
_ELSE_IF_BRIDGE = re.compile(r"^(\s*)\}\s*(else\s+if\b.*)$")


def _split_else_if_bridge(line: str) -> tuple[str, Optional[str]]:
    """Split `} else if` into a closing brace line and the remaining else-if line."""
    body = line.rstrip("\n")
    match = _ELSE_IF_BRIDGE.match(body)
    if not match:
        return line, None
    indent, rest = match.group(1), match.group(2)
    close = f"{indent}}}\n"
    return f"{indent}{rest}\n", close


def _find_closest_line(lines: Sequence[str], needle: str, start: int = 0) -> tuple[int, str, float]:
    """Finds the most similar line in the file to provide as a debug hint."""
    import difflib
    best_idx = -1
    best_ratio = 0.0
    best_line = ""
    needle_clean = needle.strip()
    
    # Check for a quoted callee name or double-quoted identifier to find exact matches of labels/functions
    identifier_match = re.search(r'["\']([^"\']+)["\']', needle)
    if identifier_match:
        ident = identifier_match.group(1)
        for i in range(len(lines)):
            if ident in lines[i]:
                return i, lines[i].strip(), 1.0

    for i in range(len(lines)):
        line_clean = lines[i].strip()
        if not line_clean:
            continue
        ratio = difflib.SequenceMatcher(None, needle_clean, line_clean).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i
            best_line = line_clean
            
    return best_idx, best_line, best_ratio


def _is_early_return_guard_line(
    lines: Sequence[str],
    idx: int,
    *,
    file_path: str = "",
    start_idx: int = 0,
) -> bool:
    """True when ``return (`` at *idx* sits inside control flow, not the root JSX return."""
    if idx < 0 or idx >= len(lines):
        return False
    if file_path:
        from apatch.boundary_ast import return_kind_at_line

        kind = return_kind_at_line(file_path, lines, idx, start_idx=start_idx)
        if kind is not None:
            return kind == "guard_return"
    if "return (" not in lines[idx] and lines[idx].strip() != "return (":
        return False
    return_indent = len(lines[idx]) - len(lines[idx].lstrip())
    for j in range(idx - 1, max(idx - 40, -1), -1):
        stripped = lines[j].strip()
        if not stripped or stripped.startswith("//"):
            continue
        line_indent = len(lines[j]) - len(lines[j].lstrip())
        if line_indent < return_indent:
            return bool(re.match(r"if\s*\(", stripped))
        if line_indent == return_indent:
            break
    return False


def analyze_strip_boundary(
    lines: Sequence[str],
    spec: StripSpec,
    start_idx: int,
    end_idx: int,
    *,
    file_path: str = "",
) -> List[str]:
    """Boundary warnings for fragile until markers and hook strips with JSX."""
    warnings: List[str] = []
    until = spec.until.strip()
    if "return (" in until or until == "return (":
        if _is_early_return_guard_line(lines, end_idx, file_path=file_path, start_idx=start_idx):
            line_no = end_idx + 1
            warnings.append(
                f"until marker {until!r} matches early-return guard at line {line_no}; "
                "prefer a root-JSX anchor such as `{backTo && (`"
            )
        else:
            for i in range(start_idx + 1, end_idx):
                if _is_early_return_guard_line(lines, i, file_path=file_path, start_idx=start_idx):
                    warnings.append(
                        f"extracted block includes early-return guard at line {i + 1}; "
                        "hook strips should omit JSX guards from the extracted region"
                    )
                    break
    removed = "".join(lines[start_idx:end_idx])
    if (spec.module_kind or "").lower() == "hook" and re.search(r"return\s*\(\s*<", removed):
        warnings.append(
            "extracted hook block contains JSX return; hook modules should return objects only"
        )
    until_stripped = spec.until.strip()
    if until_stripped in ("};", "}", "    };", "  };") or (
        re.fullmatch(r"^\s*\};\s*$", spec.until or "")
    ):
        warnings.append(
            f"until marker {spec.until!r} is a generic closer; prefer end_before root "
            "'return (' or a unique comment anchor (confidence usually < 0.45)"
        )
    try:
        from apatch.boundary_ranker import assess_until_marker

        assessment = assess_until_marker(file_path, lines, spec.start, spec.until)
        if assessment.get("unstable"):
            msg = (
                f"STRIP_BOUNDARY_UNSTABLE: until {spec.until!r} confidence="
                f"{assessment.get('confidence')} kind={assessment.get('kind')}"
            )
            rec = assessment.get("recommended_until")
            if rec:
                msg += f"; prefer end_before {rec!r} (line {assessment.get('recommended_line')})"
            warnings.append(msg)
    except Exception:
        pass
    return warnings


def suggest_until_candidates(
    lines: Sequence[str],
    start_idx: int,
    *,
    file_path: str = "",
) -> List[tuple[int, str]]:
    """Suggest boundary candidates; AST-first via tree-sitter, heuristic fallback."""
    if file_path:
        from apatch.boundary_ast import suggest_until_from_ast

        ast_hits = suggest_until_from_ast(file_path, lines, start_idx)
        if ast_hits:
            return ast_hits
    return _suggest_until_candidates_heuristic(lines, start_idx, file_path=file_path)


def _suggest_until_candidates_heuristic(
    lines: Sequence[str],
    start_idx: int,
    *,
    file_path: str = "",
) -> List[tuple[int, str]]:
    from apatch.boundary_scope import resolve_forward_scan_end

    candidates: List[tuple[int, str]] = []
    return_hits: List[tuple[int, str]] = []
    limit = resolve_forward_scan_end(lines, start_idx, file_path=file_path)
    for i in range(start_idx + 1, limit):
        line = lines[i].strip()
        if not line:
            continue
        if re.search(r"\breturn\s*\(", line):
            kind = "guard_return" if _is_early_return_guard_line(
                lines, i, file_path=file_path, start_idx=start_idx
            ) else "root_return"
            return_hits.append((i + 1, f"{line} [{kind}]"))
            continue
        if len(candidates) >= 6:
            continue
        if "else if" in line:
            candidates.append((i + 1, line))
        elif re.search(r"\bkey\s*:\s*['\"]", line):
            candidates.append((i + 1, line))
        elif line.startswith("//") and ("=== " in line or "--- " in line or "block" in line.lower()):
            candidates.append((i + 1, line))
        elif re.search(r"\{[^{}]*&&\s*\(", line):
            candidates.append((i + 1, f"{line} [jsx_anchor]"))
        elif line == "}":
            candidates.append((i + 1, line))
    for hit in return_hits:
        if hit not in candidates:
            candidates.append(hit)
    return candidates


def _is_line_boundary_marker(needle: str) -> bool:
    """Short JSX/ternary closers must match whole trimmed lines, not nested indentation."""
    stripped = needle.strip()
    if not stripped:
        return False
    if stripped in (")}", ") : (", ") :", ")", "}", "})"):
        return True
    return bool(re.match(r"^[\)\}\s:]+$", stripped) and len(stripped) <= 8)


def _is_indented_return_boundary(needle: str) -> bool:
    stripped = needle.strip()
    return bool(needle[:1].isspace() and stripped.startswith("return"))


def _find_until_line(
    lines: Sequence[str],
    needle: str,
    start: int = 0,
    *,
    suggest_until: bool = False,
) -> int:
    """Locate until boundary; prefer full-line matches for short structural closers."""
    if _is_indented_return_boundary(needle):
        target = needle.rstrip()
        for i in range(start, len(lines)):
            if lines[i].rstrip().startswith(target):
                return i
    if _is_line_boundary_marker(needle):
        target = needle.strip()
        needle_indent = len(needle) - len(needle.lstrip())
        if needle_indent > 0:
            for i in range(start, len(lines)):
                line = lines[i]
                if line.rstrip() == needle.rstrip():
                    return i
                if line.strip() == target and (len(line) - len(line.lstrip())) == needle_indent:
                    return i
        else:
            last = -1
            for i in range(start, len(lines)):
                if lines[i].strip() == target:
                    last = i
            if last != -1:
                return last
    return _find_line(lines, needle, start=start, suggest_until=suggest_until)


def _find_line(lines: Sequence[str], needle: str, start: int = 0, suggest_until: bool = False) -> int:
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i
    
    # Exact match failed, search for closest suggestion
    best_idx, best_line, ratio = _find_closest_line(lines, needle, start)
    hint = ""
    if best_idx != -1 and ratio > 0.35:
        hint = f"\n  💡 Hint: Nearest matching line found on line {best_idx + 1}: {best_line!r}"
        
    if suggest_until and start > 0:
        candidates = suggest_until_candidates(lines, start, file_path="")
        if candidates:
            hint += "\n  🔍 Suggested boundary 'until' candidates:"
            for idx, desc in candidates:
                hint += f"\n    - Line {idx}: {desc!r}"
                
    raise ValueError(f"marker not found: {needle!r}.{hint}")


def _until_line_is_root_return(line: str) -> bool:
    return bool(re.search(r"^\s*return\s*\(", line or ""))


def _replace_trailing_root_return(replacement: List[str]) -> List[str]:
    """Drop trailing ``return (`` from replace when until keeps that line in parent."""
    out = list(replacement)
    while out and not out[-1].strip():
        out.pop()
    if out and _until_line_is_root_return(out[-1]):
        out.pop()
        while out and not out[-1].strip():
            out.pop()
    return out


def trim_trailing_noise(lines_chunk: List[str]) -> List[str]:
    """Trim empty lines and comments from the end of the extracted block."""
    out = list(lines_chunk)
    while out:
        last_line = out[-1].strip()
        if not last_line or last_line.startswith("//") or last_line.startswith("#"):
            out.pop()
        else:
            break
    return out


def apply_strip(lines: List[str], spec: StripSpec, *, file_path: str = "") -> StripResult:
    try:
        start_idx = _find_line(lines, spec.start)
        
        # Check for dynamic until marker
        if spec.until in ("next_else_if", "@next_else_if"):
            end_idx = -1
            for i in range(start_idx + 1, len(lines)):
                if "else if" in lines[i]:
                    end_idx = i
                    break
            if end_idx == -1:
                raise ValueError("dynamic marker 'next_else_if' not found forward from start marker")
        else:
            end_idx = _find_until_line(
                lines, spec.until, start=start_idx + 1, suggest_until=True
            )
    except ValueError as e:
        return StripResult(spec, -1, -1, 0, False, str(e))

    # When the start marker is `} else if (...)`, the leading `}` closes the
    # previous arm. Preserve that close before removing the extracted block.
    _, start_close = _split_else_if_bridge(lines[start_idx])
    if start_close is not None:
        lines.insert(start_idx, start_close)
        start_idx += 1
        end_idx += 1

    replacement: List[str] = []
    if spec.replace:
        replacement = spec.replace.splitlines(keepends=True)
        if replacement and not replacement[-1].endswith("\n"):
            replacement[-1] += "\n"
        if end_idx < len(lines) and _until_line_is_root_return(lines[end_idx]):
            replacement = _replace_trailing_root_return(replacement)
            if replacement and not replacement[-1].endswith("\n"):
                replacement[-1] += "\n"

    boundary_warnings = analyze_strip_boundary(
        lines, spec, start_idx, end_idx, file_path=file_path
    )

    removed = end_idx - start_idx
    removed_content_lines = trim_trailing_noise(lines[start_idx:end_idx])
    removed_content = "".join(removed_content_lines)
    lines[start_idx:end_idx] = replacement

    # The until marker line is kept; drop a stray `}` when it is `} else if (...)`.
    until_idx = start_idx + len(replacement)
    if until_idx < len(lines):
        normalized, _ = _split_else_if_bridge(lines[until_idx])
        if normalized != lines[until_idx]:
            lines[until_idx] = normalized
        # Hook replace: until is often `}, [deps]);` — drop orphan closer left in parent.
        if spec.replace and re.search(r"\buse[A-Z]\w*\s*\(", spec.replace):
            if _is_hook_until_closer(lines[until_idx]):
                lines.pop(until_idx)

    return StripResult(
        spec,
        start_idx + 1,
        end_idx,
        removed,
        True,
        removed_content=removed_content,
        boundary_warnings=boundary_warnings,
    )


def apply_strips(
    file_path: Union[str, Path],
    specs: Sequence[StripSpec],
    *,
    dry_run: bool = False,
) -> tuple[List[str], List[StripResult]]:
    path = Path(file_path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    ordered = sorted(enumerate(specs), key=lambda x: _find_line_safe(lines, x[1].start), reverse=True)

    results: List[StripResult] = []
    for _, spec in ordered:
        result = apply_strip(lines, spec, file_path=str(path))
        results.append(result)

    results.reverse()

    if not dry_run:
        path.write_text("".join(lines), encoding="utf-8")

    return lines, results


def _find_line_safe(lines: Sequence[str], needle: str) -> int:
    try:
        return _find_line(lines, needle)
    except ValueError:
        return -1


def load_strip_manifest(manifest_path: Union[str, Path]) -> List[StripSpec]:
    path = Path(manifest_path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as e:
            raise RuntimeError("PyYAML required for YAML manifests: pip install pyyaml") from e
        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)

    if isinstance(data, dict):
        data = data.get("strips", data.get("blocks", []))
    if not isinstance(data, list):
        raise ValueError("manifest must be a list of strip specs or {strips: [...]}")

    specs: List[StripSpec] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"invalid strip entry: {item!r}")
        until = item.get("until") or item.get("end_before")
        if not until:
            raise ValueError(f"strip entry requires 'until' or 'end_before': {item!r}")
        specs.append(
            StripSpec(
                start=item["start"],
                until=until,
                replace=item.get("replace", ""),
                label=item.get("label", ""),
                export=item.get("export", ""),
                register=item.get("register", ""),
                target_module=item.get("target_module", ""),
                module_kind=item.get("module_kind", ""),
                parent_import=item.get("parent_import", ""),
                parent_wire=item.get("parent_wire", ""),
                verify_command=item.get("verify_command", ""),
                exclude_from_compile=list(item.get("exclude_from_compile", []) or []),
                route_from=item.get("route_from", ""),
                route_to=item.get("route_to", ""),
            )
        )
    return specs
