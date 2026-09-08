# SPEC-CONTRIB-PIPE-1 — Contribution pipe: apatch → HC hand-off

> **Status:** Implemented 2026-06-19; owner-sync quarantine amendment 2026-07-16; storage/verification reconciliation amendment 2026-07-18 — `apatch/contribution_export.py`, R1–R6 green; lint + rfp coverage PASS. HC-side drain wired (`contribution_drain.py`), production e2e green. · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONTRIB-PIPE-1`
> **Anchors:** [RFP-028](../RFP-028-avatar-wiring.md) (A28-D) · [Avatar Architecture Canon §7](../AVATAR-ARCHITECTURE-CANON.md)

## 0. Motivation

Signed contribution receipts must travel from apatch to the HC store **without loss
and without duplicates**, with no broker required. `apatch.contribution_export` copies
receipts from the per-identity store into an append-only outbox directory that the HC
ingest drains — durable-first (RFP-028 §3.4): the outbox dir is the source of truth;
Kafka, if present, is best-effort fan-out.

## R0 RFP traceability gate (meta)

RFP-028 acceptance rows map to the requirements below. This spec owns A28-D; the rest
are waivered to sibling specs (multi-spec RFP).

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A28-D | R1 | covered |
| A28-D | R2 | covered |
| A28-D | R3 | covered |
| A28-D | R4 | covered |
| A28-D | R5 | covered |
| A28-D | R6 | covered |
| A28-A | — | waiver: implemented in SPEC-AVATAR-CONTRACT-1 |
| A28-B | — | waiver: implemented in SPEC-CONTRIB-TIMESHEET-1 |
| A28-C | — | waiver: implemented in SPEC-AVATAR-CONTRACT-1 |
| A28-E | — | waiver: implemented in HC SPEC-HC-CONTRIB-INGEST-1 |
| A28-F | — | waiver: governed re-attestation in SPEC-CONTRIB-TIMESHEET-1 |
| A28-G | — | waiver: implemented in HC SPEC-AVATAR-VIEW-1 |
| A28-H | — | waiver: manual end-to-end demo, out of MVP scope |

(verify: apatch rfp coverage --rfp RFP-028 --spec SPEC-CONTRIB-PIPE-1)

## R1 Export copies receipts to the outbox

`export_pending(store_dir, outbox_dir)` copies every signed receipt from the
per-identity store into the append-only outbox and returns the new `event_id`s.

(verify: python3 -m pytest tests/test_contribution_export.py::test_r1_export_copies_events -q)

## R2 Hand-off is idempotent (no duplicates)

An event already present in the outbox (by `<event_id>.json`) is skipped; re-running
the export hands off nothing new. Loss- and duplicate-free.

(verify: python3 -m pytest tests/test_contribution_export.py::test_r2_export_is_idempotent -q)

## R3 Content is preserved byte-faithfully

The exported event preserves the signed content (`proof_ref`, `avatar_id`,
`signature`, …) so it still verifies downstream.

(verify: python3 -m pytest tests/test_contribution_export.py::test_r3_export_preserves_content -q)

## R4 Empty store is a safe no-op

Exporting from a missing/empty store returns an empty list and does not fail
(durable-first: nothing to hand off is not an error).

(verify: python3 -m pytest tests/test_contribution_export.py::test_r4_empty_store_is_noop -q)

## R5 Permanent rejection is quarantined, not retried forever

Owner sync writes a content-bound terminal receipt for event-specific permanent
rejections (`tracker_rejected`, `event_conflict`). The event remains absent from HC and
visible in source-event reconciliation, but it no longer blocks later valid evidence.
`avatar sync` reports both newly quarantined and total quarantined counts. Transport
and service failures remain pending and fail the sync so they are retried.

(verify: python3 -m pytest tests/test_contribution_export.py::test_sync_quarantines_permanent_rejection_and_continues_batches tests/test_contribution_export.py::test_sync_retries_transient_tracker_failure -q)

## R6 Reconciliation proves storage and verification separately

Both direct service delivery and owner-scoped TrustChain Avatar delivery partition
the complete durable event set into missing, present-and-verified,
present-but-unverified and identity-conflict rows. Missing and unverified rows are
replayed through canonical idempotent ingest. A post-delivery reconciliation, rather
than an upload ACK, determines the repaired/reverified counts and completion state.
Legacy v1 events without top-level `avatar_id` remain in scope through their signed
`identity.key_id`; another identity is never included.

Reconciliation also reports a present event whose exact signed envelope is absent.
apatch replays that event through the same idempotent ingest so Tracker can rebuild
later session/project/review read-models without changing or duplicating the accepted
ledger fact.

(verify: python3 -m pytest tests/test_contribution_delivery.py::test_sync_replays_present_events_until_tracker_verifies_them tests/test_contribution_delivery.py::test_sync_replays_verified_event_to_restore_full_signed_envelope tests/test_contribution_delivery.py::test_sync_reconciles_v1_identity_scoped_event_without_avatar_id tests/test_contribution_export.py::test_sync_replays_owner_event_until_tracker_verifies_it -q)
