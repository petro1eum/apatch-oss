"""SPEC-CONCEPT-C4-1 — the C4 axis (RFP-034 §3.7): concepts carry an architectural level
(context/container/component/code) and the graph groups them into C4 layers."""
import os

from apatch.concept_compile import compile_concept_graph, render_concept_graph_md

CONCEPTS = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "avatar-layer.md")
INDEX = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "index.md")

_GOOD = """<!-- @cid:cpt_x -->
```concept
name: X
c4: component
```
A component.
"""
_BAD = """<!-- @cid:cpt_y -->
```concept
name: Y
c4: bogus
```
Not a real level.
"""


def _graph():
    with open(CONCEPTS, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_compile_parses_and_validates_c4():
    """R1: a concept's `c4` level is parsed; an unknown level is an error, never silent."""
    g = compile_concept_graph(_GOOD)
    assert g["concepts"]["cpt_x"]["c4"] == "component"
    assert g["errors"] == []
    bad = compile_concept_graph(_BAD)
    assert any("unknown c4" in e for e in bad["errors"]), bad["errors"]


def test_r2_render_groups_into_c4_layers():
    """R2: the Mermaid graph groups nodes into C4 subgraphs, the summary reports the
    distribution, and each section shows its level."""
    md = render_concept_graph_md(_graph(), title="x")
    assert 'subgraph c4_component["Component"]' in md
    assert 'subgraph c4_context["Context"]' in md
    assert "C4 (§3.7):" in md
    # the contribution_event node is inside the Component layer
    assert "C4: Component" in md
    # nodes are emitted inside subgraphs (indented) and closed with `end`
    assert "  end" in md


def test_r3_served_index_has_c4_layers():
    """R3: the committed docs/concepts/index.md (served at /concepts/) shows the C4 layers."""
    with open(INDEX, encoding="utf-8") as fh:
        page = fh.read()
    assert "subgraph c4_" in page
    assert "C4 (§3.7):" in page