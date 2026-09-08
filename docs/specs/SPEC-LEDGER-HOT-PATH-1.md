# SPEC-LEDGER-HOT-PATH-1 -- Constant-time governed notarization

> **Status:** Implemented · 5/5 attested (2026-07-14) · **Owner:** APatch
> **apatch artifact:** `spec:SPEC-LEDGER-HOT-PATH-1`
> **RFP:** `RFP-038`

## 0. Motivation

Normal governed write cost must be independent of historical ledger size.

## R1 Single-object commit receipt

Validate the appended record, signature, length transition, and HEAD without a
historical iterator.

(verify: python3 -m pytest tests/test_ledger_hot_path.py::test_commit_receipt_does_not_scan_history -q)

## R2 One notarization per apply chunk

Multiple successful patch steps produce one mutation payload with every
surviving changed file.

(verify: python3 -m pytest tests/test_ledger_hot_path.py::test_apply_batches_notarization_once -q)

## R3 Cached normal verification

Normal notarization verification uses the index and HEAD only.

(verify: python3 -m pytest tests/test_ledger_hot_path.py::test_verify_notarization_hot_path_does_not_scan_history -q)

## R4 Explicit full rebuild remains available

Explicit rebuild walks the ledger once and still detects content drift.

(verify: python3 -m pytest tests/test_ledger_hot_path.py::test_explicit_rebuild_still_scans_and_detects_drift -q)

## R5 Operator-visible contract

Doctor and performance docs state chunk granularity and explicit audit.

(verify: python3 -m pytest tests/test_ledger_hot_path.py::test_doctor_reports_chunk_notarization -q)

## Non-goals

- Skipping Ed25519 verification.
- Removing full-history audit.
