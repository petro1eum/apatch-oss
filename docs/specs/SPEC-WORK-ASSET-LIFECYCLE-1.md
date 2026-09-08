# SPEC-WORK-ASSET-LIFECYCLE-1 — Governed promotion and use tracking

> **apatch artifact:** `spec:SPEC-WORK-ASSET-LIFECYCLE-1`

Implements RFP-031 Phase 2 and the first executable slice of the
[Avatar Utility Contract](../AVATAR-UTILITY-CONTRACT.md) (AUC-1 §5–§6, R-AUC-1/R-AUC-2):
governed promotion states, ledger-backed transitions, and signed session use
records over the Phase-1 WorkAsset read model (SPEC-WORK-ASSET-INDEX-1). State is
derived from signed ledger events — never a writable field. This spec deliberately
stops before suggestion/apply integration (SPEC-WORK-ASSET-SUGGEST-1), export
changes, HC ingest, or economics.

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A31-A | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-B | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-C | R1 | covered |
| A31-D | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-E | — | waiver: deferred to SPEC-WORK-ASSET-SUGGEST-1 |
| A31-F | R3 | covered |
| A31-G | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-H | — | waiver: deferred to HC consumer spec SPEC-AVATAR-WORK-ASSETS-1 |
| A31-I | — | waiver: implemented in SPEC-WORK-ASSET-INDEX-1 |
| A31-J | — | waiver: deferred to later integration-status slice |
| A31-K | R5 | covered |
| A31-L | — | waiver: deferred until suggest/export shape settles |

## R1 Ledger-derived promotion states

Lifecycle state MUST be derived by replaying signed `apatch_work_asset_promote`
events (ordered by timestamp, then op id) and applying only the RFP-031 §5.2
transitions: `candidate→accepted`, `candidate→rejected`, `accepted→deprecated`,
`accepted→superseded` (with `superseded_by`). Invalid transitions MUST be refused
at write time and deterministically ignored at fold time — the same ledger always
yields the same states. Events anchor on the stable `spec_id` (the Phase-1
`asset_id` hash shifts as attestations land) and record `asset_id` only as a
snapshot. There is no writable lifecycle field or state file.

(verify: `python3 -m pytest tests/test_work_asset_lifecycle.py::test_r1_promotion_states_ledger_derived -q`)

## R2 Method required for acceptance (AUC-1 R-AUC-1)

Promotion to `accepted` MUST require a method file (default
`docs/work_assets/<spec_id>.method.md`) that is non-empty, states its
**procedure**, **checks**, and **contraindications/boundaries** (en/ru markers),
and passes the content-safety boundary (no secrets, no economics terms). The
method's sha256 MUST be pinned inside the signed promotion event. Acceptance
without a valid method MUST be refused and nothing committed.

(verify: `python3 -m pytest tests/test_work_asset_lifecycle.py::test_r2_method_required_for_acceptance -q`)

## R3 Signed use records

A session use record MUST be committed as a signed `apatch_work_asset_use` event
carrying: `spec_id` (+ optional `asset_id` snapshot), session id, `spec_refs`,
`proof_ref`, the verification result (`{command, ok}`), and the
applied-`unchanged`-vs-`adapted` flag (RFP-031 §5.4). The payload MUST NOT
contain PI/GPI/Creator Bonus/clearing/escrow/marketplace/price fields — the
economic boundary of SPEC-WORK-ASSET-INDEX-1 R5 applies to lifecycle payloads.

(verify: `python3 -m pytest tests/test_work_asset_lifecycle.py::test_r3_use_record_shape_and_boundary -q`)

## R4 Reuse derived from the ledger (AUC-1 R-AUC-2)

`reuse.count` and `reuse.last_used_at` MUST be folded from signed
`apatch_work_asset_use` events — never a stored constant. With zero use events
the index MUST expose exactly `{"count": 0, "last_used_at": null}` (byte-compatible
with the SPEC-WORK-ASSET-INDEX-1 R1 assertion).

(verify: `python3 -m pytest tests/test_work_asset_lifecycle.py::test_r4_reuse_from_ledger -q`)

## R5 Recallable flag

The index MUST expose `recallable: true` only for assets that are `accepted` AND
carry a pinned method ref — these participate in recall (AUC-1 §5); everything
else is visible-only (`recallable: false`), including a hand-forged `accepted`
event without a method. The pinned `method_ref {path, sha256}` MUST be exposed on
the asset.

(verify: `python3 -m pytest tests/test_work_asset_lifecycle.py::test_r5_recallable_flag -q`)

## Non-goals

- Intent-based suggestion / RecallBundle assembly — SPEC-WORK-ASSET-SUGGEST-1 (Phase 3).
- Export-bundle changes and HC ingest — SPEC-WORK-ASSET-EXPORT-1 / HC consumer specs.
- Economic interpretation of reuse — Layer 3 (HC), never in apatch payloads.
- CLI/MCP promotion surfaces — follow-up slice (write-path functions are module-level here).
