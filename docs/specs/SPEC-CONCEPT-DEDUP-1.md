# SPEC-CONCEPT-DEDUP-1 — Concept dedup: raise candidates, human cuts

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-DEDUP-1`
> **Anchors:** [RFP-034 §B.2 / §3.3](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-COMPILE-1](./SPEC-CONCEPT-COMPILE-1.md) · [Avatar Architecture Canon §0/§5](../AVATAR-ARCHITECTURE-CANON.md)

## 0. Motivation

The tool for "a pile of docs collected together": when the same entity lives under
several names, `dedup_candidates(graph)` **raises merge candidates to the human** — it
never merges silently. This is the canon's own pain made checkable (Agent ×4, Identity
×7): the machine shows the asymmetry and the candidates; the human cuts (§3.3).

Signals: a shared name/alias (case-insensitive) or an overlapping `realized_by` anchor.
A confirmed merge records a `same_as` edge and keeps one canonical `concept-id` — that
step is human, out of scope here.

## R1 Shared name/alias raises a candidate

Two concepts sharing a name or alias are returned as a candidate `{a, b, reason,
evidence}` — e.g. "Платёжный шлюз" and "Процессор транзакций" both carry the alias
"payment gateway" (canon §3.3): one entity or two? The machine raises it, doesn't decide.

(verify: python3 -m pytest tests/test_concept_dedup.py::test_r1_shared_name_raises_candidate -q)

## R2 Shared realized_by raises a candidate

Two concepts pointing at the same `realized_by` code anchor are raised as a candidate
(reason `shared realized_by`, with the anchor as evidence).

(verify: python3 -m pytest tests/test_concept_dedup.py::test_r2_shared_realized_by_raises_candidate -q)

## R3 Never auto-merge (human-in-loop boundary)

Distinct concepts raise no candidates; and when candidates DO exist, the graph is left
untouched — both concepts stay separate, no `same_as` is written. Merging is the human's
decision (RFP-024 autonomy boundary), never the machine's.

(verify: python3 -m pytest tests/test_concept_dedup.py::test_r3_no_auto_merge -q)

## Non-goals

- Not the confirmation/merge step itself (write `same_as`, collapse ids) — that is the human-driven CLI follow-up.
- Not fuzzy/embedding similarity — exact name/alias + realized_by overlap first; smarter signals later.
- Not running over the whole HC pile — that needs HC docs expressed in fence format first.