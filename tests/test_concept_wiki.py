"""SPEC-CONCEPT-WIKI-1 — the concept page is a navigable web, not a static picture
(RFP-034 §B.7): clickable Mermaid nodes, cross-linked relations, backlinks."""
import os

from apatch.concept_compile import compile_concept_graph, render_concept_graph_md

_HERE = os.path.dirname(__file__)
CONCEPTS = os.path.join(_HERE, "..", "docs", "concepts", "avatar-layer.md")
INDEX = os.path.join(_HERE, "..", "docs", "concepts", "index.md")


def _graph():
    with open(CONCEPTS, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_clickable_nodes_and_anchors():
    """R1: every concept node is clickable (Mermaid `click` -> its anchor) and every
    section carries the matching explicit `{#cid}` anchor — the diagram navigates."""
    md = render_concept_graph_md(_graph())
    for cid in ("cpt_signed_fact", "cpt_contribution_event", "cpt_avatar"):
        assert 'click %s "#%s"' % (cid, cid) in md, "node not clickable: " + cid
        assert "{#%s}" % cid in md, "no section anchor: " + cid


def test_r2_relations_and_backlinks_are_links():
    """R2: relations render as links to the target section, and the reverse edge appears
    as a backlink on the target — the web is traversable both ways."""
    md = render_concept_graph_md(_graph())
    # forward: contribution_event depends_on signed_fact, rendered as a link
    assert "depends_on → [Подписанный факт работы](#cpt_signed_fact)" in md
    # backward: signed_fact shows it is referenced by contribution_event
    assert "**Упоминается в:**" in md
    assert "[ContributionEvent](#cpt_contribution_event) ← depends_on" in md


def test_r3_generated_index_is_wiki():
    """R3: the committed docs/concepts/index.md (what MkDocs serves) carries the clickable
    nodes and cross-links — the live site is the wiki, not a promise."""
    with open(INDEX, encoding="utf-8") as fh:
        page = fh.read()
    assert 'click cpt_avatar "#cpt_avatar"' in page
    assert "{#cpt_contribution_event}" in page
    assert "](#cpt_" in page  # at least one cross-link