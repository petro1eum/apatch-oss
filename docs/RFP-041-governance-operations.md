# RFP-041 -- Low-friction Governed Operations

> **Status:** Accepted for implementation (August 21, 2026)
> **Scope:** session identity, finalization, artifact isolation, differential verify, and recovery
> **Executable contract:** `docs/specs/SPEC-GOVERNANCE-OPERATIONS-1.md`

## Context

APatch is valuable at high-risk boundaries, but its own orchestration must not
become an additional source of ambiguity, stale locks, false rollback, or manual
repair. A two-day workload in `@communicate` produced 117 sessions and 3717
runtime artifacts; 2756 were unclassified and retained history exceeded policy
by 12x. Operators also observed ambiguous active sessions, stale leases,
cross-session requirement collisions, rollback caused by a pre-existing red
baseline, the historical 109-tool full MCP surface, and manual workspace registration.

The product response is not to weaken cryptographic, authority, migration, or
release governance. It is to make one governed operation deterministic and
self-cleaning, while allowing low-risk work to be attested at a meaningful
slice boundary.

## Decision

Every manual stateful lifecycle operation SHALL be bound to one explicit
governed-session capability. Intent-level orchestration may create and carry
that capability internally; workspace-global hygiene is not session-owned.
Session finalization SHALL be idempotent and SHALL converge the
session state, lane registration, writer lease, and owned temporary artifacts
to a closed state after retries.

Mutable runtime artifacts SHALL be physically namespaced by workspace, lane,
spec content hash, requirement, and session. A session SHALL never overwrite or
clean another session's requirement state.

Verification used to decide rollback SHALL compare the post-change result with
a pre-mutation baseline captured by the same governed operation. Existing
failures do not cause rollback; only newly introduced failures do.

Recovery SHALL be one operation over one exact session. It may reconcile state,
release stale owned locks, rotate the capability, rebind green stale evidence,
run safe GC, or close an already completed session. It SHALL never steal a live
foreign lease or mutate another session.

## Operating policy

APatch remains mandatory for authority, cryptography, grants/revoke, database
schema changes, and release gates. Low-risk UI copy, localization, narrow tests,
and safe refactors MAY use ordinary editing and tests, then enter one governed
attestation at the delivery-slice boundary.

## Acceptance

| ID | Requirement | Level |
|---|---|---|
| GO-P0-1 | Every manual stateful lifecycle MCP command accepts `governed_session_id`; capability-bound commands also require the current session token, and ambiguous omission fails without changing state. Intent-level orchestration carries the capability internally. | MUST |
| GO-P0-2 | `session_end` is an idempotent finalizer: retrying an exact session converges ended state, lane unregister, owned lease release, temporary deletion, and cleanup journal completion. | MUST |
| GO-P0-3 | Runtime artifacts are collision-proof across `workspace + lane + spec hash + requirement + session`; foreign session/Rk state cannot be overwritten, consumed, or removed. | MUST |
| GO-P0-4 | Apply verification automatically captures a pre-mutation baseline and rolls back only for new failures; the captured baseline is scoped to the exact session and verify command. | MUST |
| GO-P0-5 | `recover(governed_session_id)` performs deterministic one-call reconciliation and returns either a usable rotated capability, a completed cleanup result, or a precise non-destructive blocker. | MUST |
| GO-P0-6 | Multi-chunk execution has one persisted authoritative cursor. `execute_next` and recovery continue the remaining chunks deterministically without requiring the caller to reconstruct or pass internal JSONL state. | MUST |
| GO-P0-7 | A `VERIFY_FAILED` result whose mutation was already rolled back resumes in an apply-capable fix-forward state with exact session-owned patch logs; resume cannot recommend an operation that its lifecycle forbids. | MUST |
| GO-P0-8 | Patch primitives fail before mutation on overlapping needles, preserve executable mode on replacement, and exclude explicitly machine-local MCP configuration from source attestation. | MUST |
| GO-P0-9 | Stateful intent-level requests accept a caller-generated idempotency key. A retry with the same operation and payload returns the one recorded outcome and a fresh exact capability when still active; no plaintext request id or session token is persisted. | MUST |
| GO-P0-10 | Finalize validates exact session ownership before verification. A file-drift-only stale Rk may bootstrap one selective verify/rebind; include/exclude filters preserve intentionally stale sibling requirements. | MUST |
| GO-P1-1 | Default MCP exposure is a 10-17 tool `compact` profile that carries read-only orientation (impact before a target is chosen, build diagnostics after a failed verify) and no tool that mutates source outside a governed session; specialized tool packs are opt-in and `full` remains available for compatibility. | SHOULD |
| GO-P1-2 | Successful finalization performs bounded safe GC and history rotation without returning thousands of artifact rows; doctor/GC compact `HISTORY_OVERFLOW`, `INFERRED_ARTIFACTS`, and `ORPHAN_EPHEMERAL` into counts, samples, and executable repairs. | SHOULD |
| GO-P1-3 | Bound workspace onboarding is automatic; sibling roaming registration has one operator command and stale registry entries are pruned or repaired explicitly. | SHOULD |
| GO-P1-4 | Production attestations can bind local APatch evidence to TrustChain Secrets identity and Platform inclusion proof; local self-signed evidence is labeled `local`, never `production`. | SHOULD |
| GO-P1-5 | Failures before session creation do not persist lane lifecycle state, and doctor/playbooks recommend only tools present in the active MCP profile. | SHOULD |

## Non-goals

- Concurrent writes to overlapping paths in one checkout. Disjoint path-set
  concurrency is specified by RFP-042.
- Automatic takeover of a live foreign session or lease.
- Making every low-risk file edit a separate governed transaction.
- Treating local self-signed evidence as a production trust root.
- Removing advanced tools from CLI or the opt-in `full` MCP profile.
