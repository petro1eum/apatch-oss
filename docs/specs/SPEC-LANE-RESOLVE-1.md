# SPEC-LANE-RESOLVE-1 — ambiguous lane resolution is deterministic, not a quarantine lane

> **apatch artifact:** `spec:SPEC-LANE-RESOLVE-1`  
> **Anchors:** [RFP-019](../RFP-019-mcp-scale-lifecycle.md) · discharges reality `REC-b21fbd7ef7f2`

## 0. Motivation

`apatch_apply_session` and `apatch_attest` carry no `spec=` kwarg, so when more than one
lane is active, `resolve_active_lane` returned `(None, error)` and `resolve_lane` routed to
a lane literally named `ambiguous` (`.apatch/lanes/ambiguous/session_state.json`). If the
active-lane count flipped between apply (1 active → real lane X, where `phase='verify'` was
persisted) and attest (2 active → `ambiguous`, stale phase), the two operations read
*different* session_state files and the lifecycle showed `applying`/`draft` — the
non-deterministic flap that forced a revert + fresh session (reality `REC-b21fbd7ef7f2`).

Explicit `spec=` already wins (it is checked first); the gap is only the no-spec,
multi-active case, which must resolve deterministically rather than to a dead lane.

## R1 multi-active lanes resolve to the most-recent lane, not 'ambiguous' (verify: python3 -m pytest tests/test_lane_resolve.py -q) (discharges: REC-b21fbd7ef7f2)

With no `spec=` bound and more than one active lane, `resolve_active_lane` returns the
most-recently-registered active lane (by `updated_at`) instead of `(None, error)`, so
`resolve_lane` routes to a real lane (the current session) and apply + attest resolve to
the same session_state. Single-active and zero-active resolution is unchanged.
