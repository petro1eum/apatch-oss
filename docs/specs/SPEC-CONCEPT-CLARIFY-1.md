# SPEC-CONCEPT-CLARIFY-1 — the human-in-loop clarify loop

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-CLARIFY-1`
> **Anchors:** [RFP-034 §4a / §3.9](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-WIKI-1](./SPEC-CONCEPT-WIKI-1.md) · [SPEC-CONCEPT-DEDUP-1](./SPEC-CONCEPT-DEDUP-1.md)

## 0. Motivation

§4a says apatch must accept loose human input and help *shape* it — not silently take it as
fact (§3.9). The machine has `guarded` (it proved this) and `grey` (nothing checks this).
This adds the inverse of an invariant: `needs_clarification` — **a human says "this is not
settled; do not treat it as done"**.

A person marks a concept/claim with a note; the machine records it in a small store next to
the other `.apatch` maps, surfaces it *not-green* (amber) on the graph with the note, and
**only a person resolves it**. It pairs with dedup (SPEC-CONCEPT-DEDUP-1): dedup raises
candidates the human cuts; clarify lets the human raise a question the machine must keep
visible until answered. Both are human-in-loop surfaces, neither auto-decides.

`apatch/concept_clarify.py` is the store; `render_concept_graph_md(..., clarify=...)` overlays
it; `apatch concept clarify` is the CLI.

## R1 Clarify store (human-owned)

`set_clarification(id, note, by=)` records `{note, by}`; `needs_clarification()` returns the
`id -> note` overlay; `resolve_clarification(id)` clears it and is idempotent.

(verify: python3 -m pytest tests/test_concept_clarify.py::test_r1_store_roundtrip -q)

## R2 Render overlays not-green

A clarified concept renders as amber `needs_clarification` with its note, and the overlay
wins over a green verify status — an open question is never shown as done.

(verify: python3 -m pytest tests/test_concept_clarify.py::test_r2_render_overlays_not_green -q)

## R3 `concept clarify` set / list / resolve

`apatch concept clarify <id> --note ...` raises it, no id lists open ones, `--resolve` clears
it; an unknown concept/claim id is rejected (no silent typos).

(verify: python3 -m pytest tests/test_concept_clarify.py::test_r3_cli_clarify_set_list_resolve -q)

## Non-goals

- Not per-act TrustChain notarization of each clarify/resolve (who/when in the ledger) — the
  store records `by`; ledger-anchoring the human act is a follow-up.
- Not gating CI/verify on open clarifications — surfacing first; a "block while unsettled"
  policy is a later choice.
- Not auto-raising clarifications from heuristics — clarify is a deliberate human act, never
  an agent default (that would re-introduce the silent-fact problem §3.9 forbids).