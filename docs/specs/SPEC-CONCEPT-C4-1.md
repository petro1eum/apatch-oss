# SPEC-CONCEPT-C4-1 — the C4 axis

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-C4-1`
> **Anchors:** [RFP-034 §3.7 / §B.7](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-WIKI-1](./SPEC-CONCEPT-WIKI-1.md)

## 0. Motivation

A flat graph of every concept is a hairball at scale. §3.7 adds the **C4 axis**: each concept
carries an architectural level — `context` (external systems/actors), `container`
(addressable deployables), `component` (the system's own parts), `code` — and the graph
groups nodes into those layers, so the reader sees the architecture, not just a mesh.

The level is one optional field on a concept fence (`c4: component`); the compiler parses and
validates it (an unknown level is an error, never silent — §A.4), and the renderer groups
nodes into Mermaid C4 subgraphs, reports the distribution, and labels each section. It is
orthogonal to colour (coverage/verify/clarify) and to the within-page web (SPEC-CONCEPT-WIKI-1).

Initial levels for the avatar layer: `signed_fact` and `avatar_economy` are **Context**
(owned by trust_chain / HC_Platform — external to apatch), `identity (key_id)` is a
**Container** (the addressing layer), `contribution_event` and `avatar` are **Components**
(apatch's own parts). These are editable judgements — disagreement is a clarify (§4a), not a bug.

## R1 Compile parses and validates the level

A concept's `c4` level is parsed onto the node; an unknown level (not context/container/
component/code) is reported as an error, never silently dropped.

(verify: python3 -m pytest tests/test_concept_c4.py::test_r1_compile_parses_and_validates_c4 -q)

## R2 Render groups into C4 layers

The Mermaid graph groups nodes into C4 subgraphs in order context→container→component→code,
the summary reports the distribution, and each concept section shows its level. Unleveled
concepts stay bare (no forced bucket).

(verify: python3 -m pytest tests/test_concept_c4.py::test_r2_render_groups_into_c4_layers -q)

## R3 The served page shows the layers

The committed `docs/concepts/index.md` (served at `/concepts/`) shows the C4 subgraph layers
and the distribution — the architecture view is live, not a promise.

(verify: python3 -m pytest tests/test_concept_c4.py::test_r3_served_index_has_c4_layers -q)

## Non-goals

- Not a strict C4 model (no enforced one-level-per-container nesting, no `System` boundary box)
  — a pragmatic layering axis, not the full Structurizr semantics.
- Not auto-inferring levels from code/SCIP — the level is an authored judgement (a human or a
  reviewed agent suggestion), kept honest by clarify when contested.
- Not a separate per-level page/nav tree — one grouped graph page first; a C4-driven nav is a
  later phase.