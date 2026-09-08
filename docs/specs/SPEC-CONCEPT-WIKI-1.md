# SPEC-CONCEPT-WIKI-1 — the concept page is a navigable web

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-WIKI-1`
> **Anchors:** [RFP-034 §B.7 / §3.2](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-RENDER-1](./SPEC-CONCEPT-RENDER-1.md)

## 0. Motivation

The render produced a Mermaid picture plus per-concept sections, but the picture was dead:
nodes weren't clickable and relations were plain text (`` `cpt_x` ``). That is "a diagram
next to some prose", not the wiki RFP-034 promises — a graph you *walk*: click a node, land
on its definition, click its relations to neighbours, see who points back.

This adds the navigation muscle to `render_concept_graph_md` (no new module): clickable
Mermaid nodes, explicit `{#cid}` section anchors, relations and backlinks rendered as
links, and claim backlinks. Colouring (coverage / live verify) is unchanged.

## R1 Clickable nodes + section anchors

Each concept node emits a Mermaid `click <node> "#<cid>"` and each section carries the
matching `{#cid}` anchor, so clicking a node lands on its definition.

(verify: python3 -m pytest tests/test_concept_wiki.py::test_r1_clickable_nodes_and_anchors -q)

## R2 Relations and backlinks are links

A relation renders as `rel → [Name](#cid)`, and the target concept shows the reverse edge
as `**Упоминается в:** [Name](#cid) ← rel` — the web is traversable both ways.

(verify: python3 -m pytest tests/test_concept_wiki.py::test_r2_relations_and_backlinks_are_links -q)

## R3 The served page is the wiki

The committed `docs/concepts/index.md` (what MkDocs serves at `/concepts/`) carries the
clickable nodes and cross-links — the live site is the wiki, regenerated from the canon.

(verify: python3 -m pytest tests/test_concept_wiki.py::test_r3_generated_index_is_wiki -q)

## Non-goals

- Not changing the colouring model (coverage guarded/grey, live verify green/red/broken) —
  that is SPEC-CONCEPT-COVERAGE-1 / -STATUS-1.
- Not the C4 nav tree or per-concept standalone pages — single graph page first (§3.7 C4 is
  a later phase); this is the within-page web.
- Not linking `realized_by` code anchors to source — those stay as code spans until a
  SCIP-backed source-link phase.