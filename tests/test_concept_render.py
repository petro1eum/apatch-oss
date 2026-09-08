"""SPEC-CONCEPT-RENDER-1 — render the concept graph as a MkDocs page (RFP-034 §B.7)."""
import os

from apatch.concept_compile import compile_concept_graph, render_concept_graph_md

_HERE = os.path.dirname(__file__)
CONCEPTS = os.path.join(_HERE, "..", "docs", "concepts", "avatar-layer.md")
INDEX = os.path.join(_HERE, "..", "docs", "concepts", "index.md")


def _graph():
    with open(CONCEPTS, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_mermaid_edges():
    """R1: render produces a Mermaid graph with the typed edges of the concept graph."""
    md = render_concept_graph_md(_graph())
    assert "```mermaid" in md and "graph LR" in md
    assert "cpt_avatar -->|consumes| cpt_contribution_event" in md
    assert "cpt_contribution_event -->|depends on| cpt_signed_fact" in md


def test_r2_section_per_concept():
    """R2: a section per concept surfaces cid, realized_by, and invariants (not hidden)."""
    md = render_concept_graph_md(_graph())
    for cid in ("cpt_signed_fact", "cpt_contribution_event", "cpt_avatar",
                "cpt_identity_key_id", "cpt_avatar_economy"):
        assert "`%s`" % cid in md, cid
    assert "SCHEMA-LOCKSTEP" in md
    assert "Воплощён в" in md


def test_r3_rendered_page_present():
    """R3: the generated page exists under docs/ (so MkDocs serves it) and shows the
    same graph — the site view is real, not a promise."""
    with open(INDEX, encoding="utf-8") as fh:
        page = fh.read()
    assert "```mermaid" in page
    assert "ContributionEvent" in page
    assert "cpt_avatar -->|consumes| cpt_contribution_event" in page