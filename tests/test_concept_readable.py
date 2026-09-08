"""SPEC-CONCEPT-READABLE-1 — reader-depth (RFP-034 §3.8): the page leads with the author's
human preamble and each concept with its definition; the rigorous guts go under a collapsible."""
import os

from apatch.concept_compile import compile_concept_graph, render_concept_graph_md

CONCEPTS = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "avatar-layer.md")
INDEX = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "index.md")


def _graph():
    with open(CONCEPTS, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_compile_captures_preamble():
    """R1: the free-text before the first fence is captured as the doc preamble, and the
    fences themselves are not swept into it."""
    g = _graph()
    assert "preamble" in g
    assert "Канонические узлы" in g["preamble"]
    assert "@cid:" not in g["preamble"]


def test_r2_render_is_reader_depth():
    """R2: the page leads with the human preamble; each concept leads with its definition;
    the guts (realized_by/relations/invariants) sit under a collapsible, indented."""
    md = render_concept_graph_md(_graph(), title="x")
    assert md.index("Канонические узлы") < md.index("```mermaid")     # human lead first
    assert '??? info "Детали' in md                                    # collapsible present
    assert any(l.startswith("    **Воплощён в:**") for l in md.splitlines())  # guts indented in
    assert "\nЕдинственный мост" in md                                 # definition stays top-level


def test_r3_served_index_is_readable():
    """R3: the committed docs/concepts/index.md leads with the preamble and hides the guts."""
    with open(INDEX, encoding="utf-8") as fh:
        page = fh.read()
    assert "Канонические узлы" in page
    assert page.index("Канонические узлы") < page.index("```mermaid")
    assert '??? info "Детали' in page