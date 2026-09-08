# SPEC-WORK-ASSET-SUGGEST-1 — Avatar Recall: suggestion and RecallBundle

> **apatch artifact:** `spec:SPEC-WORK-ASSET-SUGGEST-1`

Implements RFP-031 Phase 3 (A31-E) and the recall edge of the
[Avatar Utility Contract](../AVATAR-UTILITY-CONTRACT.md) (AUC-1 §3–§4): given a new
task's intent, apatch suggests the owner's proven WorkAssets with explainable
match reasons and hands the agent a compact, money-free **RecallBundle** whose
next step always routes into a normal governed session. Builds on
SPEC-WORK-ASSET-INDEX-1 (read model) and SPEC-WORK-ASSET-LIFECYCLE-1
(recallable = accepted + pinned method). This spec deliberately stops before
CLI/MCP wiring, export changes, and HC ingest.

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A31-A | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-B | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-C | — | waiver: implemented in SPEC-WORK-ASSET-LIFECYCLE-1 |
| A31-D | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-E | R1 | covered |
| A31-F | — | waiver: implemented in SPEC-WORK-ASSET-LIFECYCLE-1 |
| A31-G | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-H | — | waiver: deferred to HC consumer spec SPEC-AVATAR-WORK-ASSETS-1 |
| A31-I | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-J | — | waiver: deferred to later integration-status slice |
| A31-K | R4 | covered |
| A31-L | — | waiver: deferred until CLI/MCP recall wiring settles |

## R1 Intent-based suggestion with explainable reasons

`suggest_work_assets(target_dir, intent, limit)` MUST rank assets
deterministically for a fixed ledger and intent (same input → same order) and
each suggestion MUST carry the RFP §5.3 shape: `asset_id`, `score`, explainable
`reasons` (the matched intent terms), and a guarded `next` step. Ranking uses
matched terms first, then ledger-backed reuse, then a stable id tiebreak — no
randomness, no LLM scoring.

(verify: `python3 -m pytest tests/test_work_asset_suggest.py::test_r1_intent_suggestion_explainable -q`)

## R2 Only recallable assets participate

Suggestion and RecallBundle assembly MUST include only `recallable` assets
(AUC-1 §5: `accepted` + pinned method). Candidates and method-less assets are
visible in the index but MUST be excluded from recall; requesting a bundle for
them MUST fail with an honest `NOT_RECALLABLE` error, not a degraded bundle.

(verify: `python3 -m pytest tests/test_work_asset_suggest.py::test_r2_only_recallable_participate -q`)

## R3 Guarded next step, read-only surface

Every suggestion and bundle MUST route its `next_action` into a normal governed
session (`apatch_session_start` with the asset as an artifact). The suggest
surface MUST NOT mutate workspace files, lifecycle state, or the ledger —
suggestions never directly apply changes (A31-E).

(verify: `python3 -m pytest tests/test_work_asset_suggest.py::test_r3_guarded_next_readonly -q`)

## R4 RecallBundle v1 — AUC-1 §4 shape and budget

`build_recall_bundle` MUST return the AUC-1 §4 distillate — `why_fit`, `method`
(inline or pointer), `context`, `evidence`, `rights`, `next_action` — and MUST
stay within the 16 KB canonical-JSON budget, falling back to a method POINTER
(never truncated prose) when inline content would exceed it. Bundles are
deterministic for a fixed ledger + intent.

(verify: `python3 -m pytest tests/test_work_asset_suggest.py::test_r4_recall_bundle_shape_and_budget -q`)

## R5 Method integrity and boundaries

The bundle MUST verify the method file on disk against the sha256 pinned in the
signed acceptance event and refuse (`METHOD_INTEGRITY`) when it drifted — a
changed method requires re-promotion, it is never silently served. The whole
bundle (including method text) MUST pass the content-safety / economic boundary
of SPEC-WORK-ASSET-INDEX-1 R5.

(verify: `python3 -m pytest tests/test_work_asset_suggest.py::test_r5_method_integrity_and_boundary -q`)

## Non-goals

- CLI/MCP recall surfaces (`apatch work-assets suggest`, `apatch_work_asset_suggest`) — follow-up wiring slice.
- Automatic application of the method — the agent applies it inside a normal governed session (verify + attest), which then emits the `apatch_work_asset_use` record (SPEC-WORK-ASSET-LIFECYCLE-1 R3).
- Semantic/LLM matching — deterministic term/evidence ranking only in this slice.
- Export/HC ingest of bundles — SPEC-WORK-ASSET-EXPORT-1 / HC consumer specs.
