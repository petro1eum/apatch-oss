"""SPEC-CONCEPT-COVERAGE-1 — guarded vs grey coverage + status colouring (RFP-034 §B.5)."""
import os

from apatch.concept_compile import (
    compile_concept_graph,
    concept_coverage,
    concept_status,
    render_concept_graph_md,
)

CONCEPTS = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "avatar-layer.md")


def _graph():
    with open(CONCEPTS, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_coverage_counts():
    """R1: coverage splits concepts into guarded (has a checkable invariant) and grey
    (none, can silently rot); they partition the set and percent is honest."""
    cov = concept_coverage(_graph())
    assert cov["total"] == 5
    assert "cpt_contribution_event" in cov["guarded"]   # carries SCHEMA-LOCKSTEP
    assert "cpt_signed_fact" in cov["grey"]              # no invariant
    assert 0 < cov["percent"] < 100
    assert len(cov["guarded"]) + len(cov["grey"]) == cov["total"]


def test_r2_status_per_concept():
    """R2: a node with an invariant (node or edge) is guarded; one without is grey."""
    g = _graph()
    assert concept_status(g["concepts"]["cpt_contribution_event"]) == "guarded"
    assert concept_status(g["concepts"]["cpt_avatar"]) == "guarded"
    assert concept_status(g["concepts"]["cpt_signed_fact"]) == "grey"


def test_r3_render_shows_coverage_and_colours():
    """R3: the rendered page surfaces the coverage line and colours nodes by status —
    grey is visible, never silent."""
    md = render_concept_graph_md(_graph())
    assert "Покрытие" in md
    assert "classDef guarded" in md and "classDef grey" in md
    assert any(line.strip().startswith("class ") and "guarded" in line
               and "cpt_contribution_event" in line for line in md.splitlines())