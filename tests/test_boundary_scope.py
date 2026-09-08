"""Scope-bounded forward scan for strip until suggestions."""

from apatch.boundary_ranker import assess_until_marker, rank_until_candidates
from apatch.boundary_scope import resolve_forward_scan_end


def _large_component_lines(handler_count: int = 200):
    lines = ["const Page: React.FC = () => {\n", "  const dispatch = useDispatch();\n"]
    lines.extend(f"  const step{i} = {i};\n" for i in range(handler_count))
    lines.append("  return (\n")
    lines.append("    <div />\n")
    lines.append("  );\n")
    lines.append("};\n")
    return lines


def test_resolve_forward_scan_end_uses_function_body_not_fixed_window():
    lines = _large_component_lines(200)
    end = resolve_forward_scan_end(lines, 1, file_path="Page.tsx")
    assert end == len(lines)
    assert end > 200


def test_rank_finds_root_return_in_large_component():
    lines = _large_component_lines(200)
    ranked = rank_until_candidates("Page.tsx", lines, 1)
    root = [c for c in ranked if c["kind"] == "root_return"]
    assert root
    assert root[0]["line"] == len(lines) - 3


def test_assess_until_root_return_stable_without_line_cap():
    lines = _large_component_lines(200)
    assessment = assess_until_marker(
        "Page.tsx",
        lines,
        "  const dispatch = useDispatch();",
        "  return (",
    )
    assert assessment["unstable"] is False
    assert assessment["kind"] == "root_return"
    assert assessment["confidence"] >= 0.85
