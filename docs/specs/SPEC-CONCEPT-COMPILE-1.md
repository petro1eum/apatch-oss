# SPEC-CONCEPT-COMPILE-1 — Concept/claim fence compiler (RFP-034 Level 2 foundation)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-COMPILE-1`
> **Anchors:** [RFP-034 §B.1 / §A.3 / §A.4](../RFP-034-concept-graph.md) · [Avatar Architecture Canon](../AVATAR-ARCHITECTURE-CANON.md)

## 0. Motivation

RFP-034 Level 1 (the MkDocs site) renders docs beautifully but leaves them a **pile**.
Level 2 turns the pile into a **web**: the canonical concept graph. This spec is its
foundation — the compiler that parses `concept` and `claim` fences into
`concept_map.json` and validates reference integrity.

A concept node (`<!-- @cid:X -->` + a ```concept``` YAML block) is the single canonical
definition of an entity (RFP-034 §3.1): `name`, `aliases`, `definition`, `realized_by`,
`relations`, `invariants`. A claim (`<!-- @sid:Y -->` + a ```claim``` block) is human
"why", referencing concepts by `concept-id` (§3.4). Addressing is by id, never by path
(§A.4) — and a dangling reference is a build error, never silent.

`apatch/concept_compile.py` exposes `compile_concept_graph(text)` (pure) and
`write_concept_map(text, out_dir)`.

## R1 Concept fence becomes a node

A `<!-- @cid:cpt_x -->` marker followed by a ```concept``` block yields a node with
`cid`, `name`, `aliases`, `definition` (the prose after the fence), `realized_by`,
`relations`, and `invariants`.

(verify: python3 -m pytest tests/test_concept_compile.py::test_r1_parse_concept_node -q)

## R2 Claim fence becomes a claim

A `<!-- @sid:y -->` marker followed by a ```claim``` block yields `sid`, `type`,
`about` (concept-ids it references), `edges`, and `verify`.

(verify: python3 -m pytest tests/test_concept_compile.py::test_r2_parse_claim -q)

## R3 Dangling references are errors

A `relation.to` or a claim `about` that names a concept not defined in the graph is
reported in `errors` — addressing is by id and every reference must resolve (§A.4).
Silence is forbidden.

(verify: python3 -m pytest tests/test_concept_compile.py::test_r3_dangling_reference_is_error -q)

## R4 Duplicate ids are errors

A repeated `cid` (or `sid`) is reported, never silently overwritten — one canonical
node per id (§3.1).

(verify: python3 -m pytest tests/test_concept_compile.py::test_r4_duplicate_id_is_error -q)

## R5 Clean graph compiles and persists

A clean document compiles to `{schema_version, concepts, claims, errors: []}`, and
`write_concept_map` persists `concept_map.json` alongside `knowledge_map.json`.

(verify: python3 -m pytest tests/test_concept_compile.py::test_r5_clean_graph_and_write -q)

## Non-goals

- Not wired into the generic `apatch compile` CLI yet — standalone module first; CLI/`knowledge_map.json` merge is a follow-up.
- Not deduplication (`apatch concept dedup`, §B.2) — that's the human-in-loop merge phase.
- Not `realized_by` resolution or edge-invariant evaluation — symbol resolution lives with SCIP/SPEC-EDGE-LOCKSTEP-1.
- Not C4 rendering / status badges / the clarify loop — later Level 2 phases.