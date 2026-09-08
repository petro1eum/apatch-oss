# SPEC-CONCEPT-MERGE-1 — the human-confirmed merge

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-MERGE-1`
> **Anchors:** [RFP-034 §B.2 / §3.3](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-DEDUP-1](./SPEC-CONCEPT-DEDUP-1.md) · [SPEC-CONCEPT-CLARIFY-1](./SPEC-CONCEPT-CLARIFY-1.md)

## 0. Motivation

Dedup (SPEC-CONCEPT-DEDUP-1) raises merge *candidates* and never merges — the machine shows
asymmetry, the human cuts. This is the cut: a human confirms "A and B are the same concept —
keep B", the machine records `A same_as B` and **collapses** the graph.

Collapse is a pure function: drop the merged node, rewrite every relation/claim reference to
the survivor, fold the merged node's name (as alias), aliases and `realized_by` into the
survivor, drop self-loops the merge creates, and record `absorbed: [...]` on the survivor.
The decision is a human-owned store (mirrors the clarify store); the collapse is deterministic.

Recording the merge closes the loop: once it is on record, dedup stops raising the pair and
the rendered graph shows one node, not two. It is never automatic — `apatch concept merge`
is a deliberate human act, like `apatch concept clarify`.

## R1 Collapse is pure and rewrites references

`collapse_graph(graph, {dropped: into})` returns a fresh graph (input untouched): the dropped
node is gone, its name/aliases/realized_by are folded into the survivor, references to it are
rewritten to the survivor, self-loops are dropped, and the survivor records what it absorbed.

(verify: python3 -m pytest tests/test_concept_merge.py::test_r1_collapse_is_pure_and_rewrites_refs -q)

## R2 A recorded merge excludes the dedup candidate

Dedup raises a duplicate pair; once a human records the merge, the collapsed graph has one
node and dedup no longer raises it — the loop closes. `record_merge` / `unmerge` are the
human-owned store; `merge_map` feeds collapse.

(verify: python3 -m pytest tests/test_concept_merge.py::test_r2_recorded_merge_excludes_dedup_candidate -q)

## R3 `concept merge` record / list / unmerge

`apatch concept merge <dropped> <into>` records it (no args lists, `--unmerge` undoes);
coverage/verify/dedup/graph/clarify then see the collapsed graph. An unknown id and a
self-merge are rejected.

(verify: python3 -m pytest tests/test_concept_merge.py::test_r3_cli_merge_list_unmerge -q)

## Non-goals

- Not rewriting the source markdown fences — the canon docs are unchanged; collapse is applied
  to the compiled graph at read time (the merge store is the record). Folding the prose back
  into one fence is a later, human-reviewed edit.
- Not per-act TrustChain notarization of the merge decision — the store records `by`; ledger
  anchoring is a follow-up (same boundary as SPEC-CONCEPT-CLARIFY-1).
- Not auto-merging high-confidence candidates — confirmation is always a human act (§3.3).