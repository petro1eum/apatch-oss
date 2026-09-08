# SPEC-CONCEPT-COVERAGE-1 — Concept coverage: guarded vs grey, on the graph

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-COVERAGE-1`
> **Anchors:** [RFP-034 §B.5 / §3.6](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-RENDER-1](./SPEC-CONCEPT-RENDER-1.md) · [SPEC-AVATAR-CONCEPTS-1](./SPEC-AVATAR-CONCEPTS-1.md)

## 0. Motivation

How much of the canon is actually protected? `concept_coverage(graph)` classifies each
concept as **guarded** (has a checkable invariant — node or edge) or **grey** (none; it
can silently rot, RFP-034 §3.6) and reports the ratio. The render then **colours the
graph by status** so grey is visible at a glance — silence is forbidden.

On the avatar layer this is honest, not flattering: 2/5 guarded (40%) — `ContributionEvent`
(carries `SCHEMA-LOCKSTEP`) and `Avatar`; the other three are grey.

## R1 Coverage counts guarded vs grey

`concept_coverage(graph)` returns `{total, guarded, grey, percent}` where guarded and grey
partition the concepts and `percent` is the guarded fraction.

(verify: python3 -m pytest tests/test_concept_coverage.py::test_r1_coverage_counts -q)

## R2 Per-concept status

`concept_status(node)` is `guarded` when the concept has a node invariant or an
invariant-bearing edge, else `grey`.

(verify: python3 -m pytest tests/test_concept_coverage.py::test_r2_status_per_concept -q)

## R3 The graph shows coverage and colours

`render_concept_graph_md` adds a coverage line and Mermaid `classDef`/`class` so guarded
nodes are coloured apart from grey — the site graph shows what is protected and what is not.

(verify: python3 -m pytest tests/test_concept_coverage.py::test_r3_render_shows_coverage_and_colours -q)

## Non-goals

- Not running the invariants (`concept verify` — does the check actually pass?) — coverage
  asks "is there a check", not "does it pass"; node-invariant evaluation is a later phase.
- Not a CI gate on a grey threshold — reporting first; gating is a config decision (RFP-034 open Q4).
- Not C4-tree colouring — flat status colouring first.