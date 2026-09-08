# SPEC-CONCEPT-RENDER-1 — Render the concept graph as a MkDocs page

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-RENDER-1`
> **Anchors:** [RFP-034 §B.7 / §3.8](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-COMPILE-1](./SPEC-CONCEPT-COMPILE-1.md) · [SPEC-AVATAR-CONCEPTS-1](./SPEC-AVATAR-CONCEPTS-1.md)

## 0. Motivation

Level 2 you can *see*: the compiled concept graph projected into the MkDocs Material site
(RFP-034 §B.7) instead of read as JSON. `render_concept_graph_md(graph)` produces a page
with a **Mermaid diagram** of the typed edges and a section per concept (definition,
`realized_by`, relations, invariants). `mkdocs.yml` enables the `mermaid` custom fence so
Material renders it as a clickable graph.

The generated page `docs/concepts/index.md` is the avatar layer's graph view — the
human surface over `SPEC-AVATAR-CONCEPTS-1`'s nodes, served at `mkdocs serve`.

## R1 Mermaid diagram of the typed edges

`render_concept_graph_md(graph)` emits a ```mermaid `graph LR` block with every concept
node and its typed edges (e.g. `cpt_avatar -->|consumes| cpt_contribution_event`).

(verify: python3 -m pytest tests/test_concept_render.py::test_r1_mermaid_edges -q)

## R2 A section per concept

The page renders one section per concept exposing its `concept-id`, `realized_by`, and
invariants (incl. `SCHEMA-LOCKSTEP`) — surfaced, not hidden.

(verify: python3 -m pytest tests/test_concept_render.py::test_r2_section_per_concept -q)

## R3 The rendered page is in the site

`docs/concepts/index.md` exists (so MkDocs serves the Concepts section as the graph view)
and shows the same Mermaid graph — the site view is real, not a promise.

(verify: python3 -m pytest tests/test_concept_render.py::test_r3_rendered_page_present -q)

## Non-goals

- Not auto-regeneration on build — the page is generated and committed; a `gen-files`
  hook to rebuild it from `concept_map.json` on every `mkdocs build` is a later step.
- Not interactive graph navigation / status colouring (guarded/grey/red) — that rides on
  `concept verify` / `coverage`, later Level 2 phases.
- Not C4-tree layout (§3.7) — flat typed-edge graph first.