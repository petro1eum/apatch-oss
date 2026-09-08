# RFP-038 -- Constant-Time Ledger Write Path

> **Status:** Implemented (July 14, 2026)
> **Scope:** TrustChain commit receipts, APatch notarization granularity, ledger audit separation, and hot-path performance
> **Executable contract:** `docs/specs/SPEC-LEDGER-HOT-PATH-1.md`

## Context

The enforced mutation path reads and JSON-decodes every ledger object before a
commit and again after it. Normal notarization verification performs another
historical walk for diagnostic counts. At 160,212 objects this adds about
11-13 seconds to every patch step. One apply chunk also signs once per textual
patch even though checkpoint and rollback ownership already live at chunk level.

## Decision

A successful TrustChain write SHALL retain an O(1) receipt for the one object it
just appended: Ed25519 signature, chain length transition, record payload, and
persisted HEAD. Normal writes MUST NOT walk historical objects.

One apply chunk SHALL produce one mutation notarization containing all surviving
changed files. Failed or rolled-back steps are excluded; failed notarization
rolls back the chunk. Normal verification uses the incremental index and HEAD.
Full traversal remains explicit via rebuild/audit/recovery.

## Acceptance

| ID | Requirement | Level |
|---|---|---|
| LH-A | Commit validation reads only the appended object and HEAD. | MUST |
| LH-B | Multiple patch steps in one chunk create one mutation notarization. | MUST |
| LH-C | Normal notarization verification performs no historical walk. | MUST |
| LH-D | Explicit rebuild still walks history and detects drift. | MUST |
| LH-E | Doctor and operator docs expose the new granularity and audit boundary. | MUST |

## Security invariants

- Ed25519 remains mandatory under enforcement.
- HEAD and the appended record must agree with the signed response.
- Missing or invalid receipt is a notarization failure.
- Full-history audit is moved, not removed.

## Non-goals

- No ledger wire-format change.
- No weakening of external inclusion proofs.
