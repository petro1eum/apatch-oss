"""SPEC-AVATAR-CONCEPTS-1 — the avatar layer expressed as a validated concept graph.

Compiles the REAL canonical concept file (docs/concepts/avatar-layer.md) with the
RFP-034 concept compiler and asserts the graph is clean and faithful to the canon.
"""
import os

from apatch.concept_compile import compile_concept_graph

DOC = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "avatar-layer.md")


def _graph():
    with open(DOC, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_canon_concepts_compile_clean():
    """R1: the real avatar concept file compiles to >=5 concepts with zero integrity
    errors — every relation.to and claim about resolves to a defined concept."""
    g = _graph()
    assert len(g["concepts"]) >= 5, list(g["concepts"])
    assert g["errors"] == [], g["errors"]


def test_r2_contribution_event_node():
    """R2: ContributionEvent is realized by the shared contract AND the apatch emitter,
    carries the SCHEMA-LOCKSTEP edge invariant, and depends on the signed fact."""
    ce = _graph()["concepts"]["cpt_contribution_event"]
    assert any("contribution_event.py:ContributionEvent" in r for r in ce["realized_by"])
    assert any("contribution.py:build_event" in r for r in ce["realized_by"])
    assert {"kind": "edge", "ref": "SCHEMA-LOCKSTEP"} in ce["invariants"]
    assert any(r["rel"] == "depends_on" and r["to"] == "cpt_signed_fact"
               for r in ce["relations"])


def test_r3_layer_flow_edges():
    """R3: the L1->L2->L3 flow is captured as typed edges — avatar consumes the
    contribution event (fold), economy consumes the avatar."""
    g = _graph()
    assert any(r["rel"] == "consumes" and r["to"] == "cpt_contribution_event"
               for r in g["concepts"]["cpt_avatar"]["relations"])
    assert any(r["rel"] == "consumes" and r["to"] == "cpt_avatar"
               for r in g["concepts"]["cpt_avatar_economy"]["relations"])


def test_r4_claim_resolves_to_concept():
    """R4: the 'one contract' decision claim resolves to a real concept and points at
    the edge that guards it."""
    g = _graph()
    c = g["claims"]["claim_one_contract"]
    assert c["about"] == ["cpt_contribution_event"]
    assert c["about"][0] in g["concepts"]
    assert c["verify"]["edge"] == "SCHEMA-LOCKSTEP"