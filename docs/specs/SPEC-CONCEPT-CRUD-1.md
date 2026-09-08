# SPEC-CONCEPT-CRUD-1 — create / update / delete over concepts

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-CRUD-1`
> **Anchors:** [RFP-034 §6 / §B.1](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-CLI-1](./SPEC-CONCEPT-CLI-1.md) · [SPEC-CONCEPT-MERGE-1](./SPEC-CONCEPT-MERGE-1.md)

## 0. Motivation

The graph had **Read** (compile / graph / coverage / verify) and two narrow human edits
(clarify, merge). Authoring a concept still meant hand-editing markdown fences. This is the
rest of CRUD — **Create / Update / Delete** — as first-class operations.

Two layers, deliberately split so a future web UI reuses the same core ("UI later, over the
same governed operations"):

1. `apatch/concept_crud.py` — **pure markdown transforms**: `add_concept` / `update_concept`
   / `remove_concept` take doc text and return new doc text. No disk, no ledger. Round-trips
   through the compiler; a duplicate-on-create or unknown-on-edit/delete is an error.
2. `apatch concept new / edit / rm` — the CLI turns a transform into a **governed needle** of
   the canon. The edit only lands (notarised) when run through
   `apatch_generate_batch → apatch_apply_session`. There is no path that mutates a concept
   while skipping apatch + TrustChain — the trust model holds for CRUD too.

## R1 Create

`add_concept(text, cid, ...)` appends a fence that round-trips through compile; a duplicate
`cid` is rejected (no silent overwrite). `apatch concept new` builds it as a governed needle.

(verify: python3 -m pytest tests/test_concept_crud.py::test_r1_create_roundtrips_and_rejects_dup -q)

## R2 Update and delete

`update_concept` edits name/c4/definition/relations/aliases/realized_by in place (re-serialised
and still compile-clean); `remove_concept` drops the fence and leaves the rest intact; an
unknown id is rejected.

(verify: python3 -m pytest tests/test_concept_crud.py::test_r2_update_and_delete -q)

## R3 Governed CLI

`apatch concept new / edit / rm` locate the doc (or take `--doc`), apply the transform, and
emit a governed needle whose applied result compiles clean. An invalid `c4` level and an
unknown id are rejected before any needle is written.

(verify: python3 -m pytest tests/test_concept_crud.py::test_r3_cli_new_edit_rm_emit_governed_needles -q)

## Non-goals

- Not a one-shot self-notarising command — CRUD emits the needle; `apply_session` notarises.
  Keeping authoring and notarisation as distinct steps is intentional (the apatch flow), and
  a `--apply` convenience that drives apply_session in-process is a later add.
- Not the browser editor — that is the "UI later" track; it will call this same pure core via
  a governed API, not a second implementation.
- Not below-fence field history — the fence is the unit; per-field provenance is the ledger's.
- Not editing claims (`@sid`) yet — concepts first; claim CRUD mirrors this later.