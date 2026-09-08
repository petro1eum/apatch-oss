"""AST-first strip boundary analysis."""

from apatch.boundary_ast import (
    analyze_function_boundaries,
    rank_from_ast,
    return_kind_at_line,
)
from apatch.boundary_ranker import assess_until_marker
from apatch.strip import analyze_strip_boundary, _is_early_return_guard_line


def _page_with_guard():
    return [
        "export function Page() {\n",
        "  const dispatch = useDispatch();\n",
        "  if (!profile) {\n",
        "    return (\n",
        "      <div>not found</div>\n",
        "    );\n",
        "  }\n",
        "  const handleSave = () => {\n",
        "    return;\n",
        "  };\n",
        "  return (\n",
        "    <div className=\"space-y-4\" />\n",
        "  );\n",
        "}\n",
    ]


def test_ast_classifies_guard_and_root_returns():
    lines = _page_with_guard()
    analysis = analyze_function_boundaries("Page.tsx", lines, 1)
    assert analysis is not None
    assert analysis.source == "ast"
    kinds = {(c.line, c.kind) for c in analysis.candidates if c.kind.endswith("_return")}
    assert (4, "guard_return") in kinds
    assert (11, "root_return") in kinds
    assert (9, "guard_return") not in kinds and (9, "root_return") not in kinds


def test_ast_ignores_nested_handler_return():
    lines = _page_with_guard()
    assert return_kind_at_line("Page.tsx", lines, 7, start_idx=1) is None


def test_rank_from_ast_prefers_root_return():
    lines = _page_with_guard()
    ranked = rank_from_ast("Page.tsx", lines, 1)
    assert ranked[0]["kind"] == "root_return"
    assert ranked[0]["source"] == "ast"
    assert ranked[0]["confidence"] >= 0.9


def test_assess_until_root_return_ast_confidence():
    lines = _page_with_guard()
    assessment = assess_until_marker(
        "Page.tsx",
        lines,
        "  const dispatch = useDispatch();",
        "  return (",
    )
    assert assessment["boundary_source"] == "ast"
    assert assessment["kind"] == "root_return"
    assert assessment["confidence"] >= 0.9
    assert assessment["unstable"] is False


def test_is_early_return_guard_line_uses_ast():
    lines = _page_with_guard()
    assert _is_early_return_guard_line(lines, 3, file_path="Page.tsx", start_idx=1) is True
    assert _is_early_return_guard_line(lines, 10, file_path="Page.tsx", start_idx=1) is False


def test_analyze_strip_boundary_warns_guard_until_with_ast():
    lines = _page_with_guard()
    from apatch.strip import StripSpec, _find_line, _find_until_line

    spec = StripSpec(
        start="const dispatch = useDispatch();",
        until="return (",
        module_kind="hook",
    )
    start_idx = _find_line(lines, spec.start)
    end_idx = _find_until_line(lines, spec.until, start=start_idx + 1)
    warnings = analyze_strip_boundary(
        lines, spec, start_idx, end_idx, file_path="Page.tsx"
    )
    assert any("early-return guard" in w for w in warnings)


def test_large_component_root_return_via_ast():
    lines = ["const Page: React.FC = () => {\n", "  const dispatch = useDispatch();\n"]
    lines.extend(f"  const step{i} = {i};\n" for i in range(200))
    lines.append("  return (\n")
    lines.append("    <div />\n")
    lines.append("  );\n")
    lines.append("};\n")

    ranked = rank_from_ast("Page.tsx", lines, 1)
    root = [c for c in ranked if c["kind"] == "root_return"]
    assert root
    assert root[0]["line"] == len(lines) - 3
