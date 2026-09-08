# SPEC-AVATAR-CONCEPTS-1 — Avatar layer as a validated concept graph

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-AVATAR-CONCEPTS-1`
> **Anchors:** [RFP-034 §A.2](../RFP-034-concept-graph.md) · [Avatar Architecture Canon](../AVATAR-ARCHITECTURE-CANON.md) · [SPEC-CONCEPT-COMPILE-1](./SPEC-CONCEPT-COMPILE-1.md) · [SPEC-EDGE-LOCKSTEP-1](./SPEC-EDGE-LOCKSTEP-1.md)

## 0. Motivation

The first real concept graph, not a sample: the Avatar Architecture Canon's core
entities expressed as addressable concept nodes (`docs/concepts/avatar-layer.md`) and
compiled by [SPEC-CONCEPT-COMPILE-1](./SPEC-CONCEPT-COMPILE-1.md). The canon prose stays
the human narrative; these nodes are what docs, specs and code reference by `concept-id`.

This closes the loop across the whole RFP-034 pilot: the `cpt_contribution_event` node
carries the `SCHEMA-LOCKSTEP` edge invariant built in
[SPEC-EDGE-LOCKSTEP-1](./SPEC-EDGE-LOCKSTEP-1.md), and the canon's L1→L2→L3 flow becomes
typed, checkable edges. It is also the template for canonicalizing HC_Capital's pile.

## R1 The canon concepts compile clean

`docs/concepts/avatar-layer.md` compiles (SPEC-CONCEPT-COMPILE-1) to ≥5 concepts with
**zero integrity errors** — every `relation.to` and claim `about` resolves to a defined
concept. The real canon graph has no dangling references.

(verify: python3 -m pytest tests/test_avatar_concepts.py::test_r1_canon_concepts_compile_clean -q)

## R2 ContributionEvent node is faithful

`cpt_contribution_event` is `realized_by` both the shared `avatar-contract` schema and
the apatch emitter, carries the `SCHEMA-LOCKSTEP` edge invariant, and `depends_on` the
signed fact — matching the canon §7 / Rule 1.

(verify: python3 -m pytest tests/test_avatar_concepts.py::test_r2_contribution_event_node -q)

## R3 Layer flow is typed edges

The canon's L1→L2→L3 flow is captured as relations: `cpt_avatar` **consumes**
`cpt_contribution_event` (the fold), and `cpt_avatar_economy` **consumes** `cpt_avatar`.

(verify: python3 -m pytest tests/test_avatar_concepts.py::test_r3_layer_flow_edges -q)

## R4 Claim references resolve to a node

The "one contract" decision claim is `about` a real concept (`cpt_contribution_event`)
and points at the edge that guards it (`SCHEMA-LOCKSTEP`) — a claim referencing the
canon by id, not re-describing it.

(verify: python3 -m pytest tests/test_avatar_concepts.py::test_r4_claim_resolves_to_concept -q)

## Non-goals

- Not the C4 render / MkDocs graph view — a later Level 2 phase.
- Not `realized_by` symbol resolution (does the file/symbol exist) — that is SCIP/edge-invariant territory.
- Not deduplication of the canon's collision list (Agent×4, Identity×7) — that is `apatch concept dedup` (§B.2), human-in-loop.