# SPEC-CONCEPT-READABLE-1 — reader-depth on the page

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-READABLE-1`
> **Anchors:** [RFP-034 §3.8 / §B.7](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-WIKI-1](./SPEC-CONCEPT-WIKI-1.md) · [SPEC-CONCEPT-C4-1](./SPEC-CONCEPT-C4-1.md)

## 0. Motivation

The page was machine-first: it opened with coverage stats and a graph, and every concept
dumped its `realized_by` / invariants / relations inline — "guts exposed". §3.8 says the
documentation must have **levels of immersion**: a human reads meaning, a developer expands
the proof. This makes the page readable without losing rigor.

Two moves:

1. **Author's preamble becomes the lead.** Free text before the first fence in a concept doc
   (which the compiler used to discard) is captured as `graph["preamble"]` and rendered as the
   page's human introduction — write loosely, apatch frames it (§4a spirit).
2. **Guts go under a collapsible.** Each concept section leads with its name + definition
   (and a clarify banner if open); the rigorous fields — `realized_by`, relations, backlinks,
   claims, invariants, C4 — move under a collapsed `??? info "Детали — для разработчика"`.

No data is hidden, only folded: the proof is one click away, the meaning is on the surface.

## R1 Compile captures the preamble

`compile_concept_graph` returns `preamble` = the text before the first `@cid`/`@sid` marker
(the fences themselves are not swept in).

(verify: python3 -m pytest tests/test_concept_readable.py::test_r1_compile_captures_preamble -q)

## R2 Render is reader-depth

The render leads with the human preamble, each concept leads with its definition, and the
guts sit under a collapsible (indented inside it). The definition stays top-level.

(verify: python3 -m pytest tests/test_concept_readable.py::test_r2_render_is_reader_depth -q)

## R3 The served page is readable

The committed `docs/concepts/index.md` leads with the preamble and folds the guts under the
collapsible — the live `/concepts/` page reads as documentation, not a data dump.

(verify: python3 -m pytest tests/test_concept_readable.py::test_r3_served_index_is_readable -q)

## Non-goals

- Not authoring the richer per-concept narrative itself — that is content, added via
  `apatch concept edit` (SPEC-CONCEPT-CRUD-1); this is the *presentation* that makes such
  prose land well.
- Not multi-page structure / per-concept standalone pages — one readable page first; a
  doc tree is a later phase.
- Not configuring the MkDocs theme — relies on the already-enabled `pymdownx.details`.